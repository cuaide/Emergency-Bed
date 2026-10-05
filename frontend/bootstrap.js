/**
 * 첫 화면부터 실제 데이터가 보이게 만드는 부트스트랩. app.js 다음에 로드된다.
 *
 * app.js 는 즉시 실행 함수라서 로드 시점에 이미 data.js 의 데모 데이터로 한 번
 * 그려 버린다. app.js 를 고치지 않고 이를 해결하려고, 여기서 백엔드 조회가 끝난 뒤
 * window.APP_DATA.hospitals 를 교체하고 hashchange 를 쏘아 다시 그리게 한다.
 * (app.js 455행: window.addEventListener('hashchange', route))
 *
 * 이 파일이 없어도 앱은 동작한다. 대신 홈 화면의 새로고침 버튼을 누를 때까지
 * 데모 데이터가 보인다.
 */
(() => {
  const cfg = window.APP_CONFIG || {};
  if (!cfg.useBackend) return;

  // 병원 데이터를 화면에 쓰는 페이지에서만 다시 그린다. 로그인·회원가입처럼
  // 입력 중인 화면을 다시 그리면 사용자가 입력한 값이 날아간다.
  const REDRAWABLE = new Set(['home', 'results', 'report', 'map', 'transport', 'ambulance']);

  function currentPage() {
    const raw = location.hash.replace(/^#/, '') || '/login';
    return raw.split('/').filter(Boolean)[0] || 'login';
  }

  async function load() {
    try {
      const rows = await window.APP_API.fetchHospitals();
      if (!Array.isArray(rows) || !rows.length) return;

      window.APP_DATA.hospitals = rows;
      // 날씨는 fetchHospitals 가 이미 같이 받아 DATA.currentWeather 에 넣는다 (api.js).
      // 폴백이 발동했는데 "백엔드에서 불러왔다"고 찍으면 디버깅을 오히려 방해한다.
      const source = window.APP_API.lastHospitalSource();
      console.info(
        source === 'backend'
          ? `[bootstrap] 백엔드에서 응급실 ${rows.length}곳을 불러왔습니다.`
          : `[bootstrap] 백엔드 대신 data.js 데모 데이터 ${rows.length}곳을 사용합니다.`
      );

      if (REDRAWABLE.has(currentPage())) {
        window.dispatchEvent(new Event('hashchange'));
      }
    } catch (err) {
      // fallbackToDemo 가 false 인 경우에만 여기로 온다. 데모 데이터로 계속 진행한다.
      console.warn('[bootstrap] 초기 병원 조회 실패, 데모 데이터로 시작합니다.', err);
    }
  }

  // 개발 중 "연결이 된 건가?"를 콘솔 한 줄로 확인할 수 있게 한다.
  window.APP_API.checkHealth()
    .then(h => console.info(`[bootstrap] 백엔드 연결 확인 — status=${h.status}, database=${h.database}`))
    .catch(err => console.warn(`[bootstrap] 백엔드에 연결하지 못했습니다 (baseUrl='${cfg.baseUrl}'): ${err.message}`));

  load();

  /**
   * 실시간 병상 갱신 구독. app.js는 고치지 않고(위 12행 원칙과 동일) DATA.hospitals에서
   * 바뀐 병원만 교체한 뒤, app.js가 노출한 APP_LIVE_REFRESH 훅으로 결과 화면만
   * 다시 계산해서 그린다 — 결과 화면이 아니면 DATA만 갱신되고 화면은 다음 방문 때 반영된다.
   */
  if (cfg.useLiveBeds) {
    window.APP_API.subscribeBedUpdates(rows => {
      const center = window.APP_API.currentSearchCenter();
      const hospitals = window.APP_DATA.hospitals;
      let changed = false;
      for (const row of rows) {
        const idx = hospitals.findIndex(h => h.id === row.hpid);
        if (idx === -1) continue;
        const prev = hospitals[idx];
        // 스트림 행(BedStatus)에는 distance_km / route 가 없다. 그대로 넣으면 거리가
        // 999로 떨어져 모든 반경 밖으로 밀리고 후보가 0곳이 된다.
        // 직전 조회 결과(raw) 위에 바뀐 병상 값만 덮어써서 거리·경로를 지킨다.
        const merged = { ...(prev.raw || {}), ...row };
        hospitals[idx] = window.APP_ADAPTER.normalizeHospital(merged, {
          center,
          spanKm: cfg.radiusKm || 20,
          rainRisk: prev.rainRisk
        });
        changed = true;
      }
      if (!changed) return;
      if (typeof window.APP_LIVE_REFRESH === 'function') window.APP_LIVE_REFRESH();
      else if (REDRAWABLE.has(currentPage())) window.dispatchEvent(new Event('hashchange'));
    });
  }
})();
