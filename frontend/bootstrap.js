
(() => {
  const cfg = window.APP_CONFIG || {};
  if (!cfg.useBackend) return;

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

      console.warn('[bootstrap] 초기 병원 조회 실패, 데모 데이터로 시작합니다.', err);
    }
  }


  window.APP_API.checkHealth()
    .then(h => console.info(`[bootstrap] 백엔드 연결 확인 — status=${h.status}, database=${h.database}`))
    .catch(err => console.warn(`[bootstrap] 백엔드에 연결하지 못했습니다 (baseUrl='${cfg.baseUrl}'): ${err.message}`));

  load();


  if (cfg.useLiveBeds) {
    window.APP_API.subscribeBedUpdates(rows => {
      const center = window.APP_API.currentSearchCenter();
      const hospitals = window.APP_DATA.hospitals;
      let changed = false;
      for (const row of rows) {
        const idx = hospitals.findIndex(h => h.id === row.hpid);
        if (idx === -1) continue;
        const prev = hospitals[idx];

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
