/**
 * 실제 지도(Tmap Web SDK) 렌더링.
 *
 * app.js 는 화면을 innerHTML 로 통째로 다시 그리기 때문에 지도 인스턴스가 매번 사라진다.
 * 그래서 "그려진 뒤에 컨테이너를 찾아 다시 붙이는" 방식으로 만든다 — app.js 의 렌더링
 * 흐름을 건드리지 않으려는 것이다.
 *
 * appKey 는 소스에 박지 않고 백엔드(/config/map → .env)에서 받아 온다.
 */
window.APP_MAP = (() => {
  const SDK_URL = 'https://apis.openapi.sk.com/tmap/jsv2?version=1&appKey=';

  // 혼잡도 코드(0~4) → 색. Tmap 기준: 0 정보없음 1 원활 2 서행 3 지체 4 정체
  const COLORS = { 0: '#9aa0b4', 1: '#22a06b', 2: '#e2a63b', 3: '#e4762f', 4: '#d63b3b' };

  let sdkPromise = null;

  function appendScript(src) {
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = src;
      script.async = false; // 순서 보장
      script.onload = resolve;
      script.onerror = () => reject(new Error(`스크립트 로드 실패: ${src}`));
      document.head.appendChild(script);
    });
  }
  function loadSdk() {
    if (window.Tmapv2 && window.Tmapv2.Map) return Promise.resolve(window.Tmapv2);
    if (sdkPromise) return sdkPromise;

    sdkPromise = window.APP_API.mapConfig()
      .then(async cfg => {
        const written = [];
        const originalWrite = document.write;
        const originalWriteln = document.writeln;
        const capture = html => {
          const re = /src=['"]([^'"]+)['"]/g;
          let m;
          while ((m = re.exec(String(html)))) written.push(m[1]);
        };
        document.write = capture;
        document.writeln = capture;
        try {
          await appendScript(SDK_URL + encodeURIComponent(cfg.tmap_app_key));
        } finally {
          document.write = originalWrite;
          document.writeln = originalWriteln;
        }

        for (const src of written) {
          await appendScript(src.startsWith('http') ? src : new URL(src, location.href).href);
        }
        if (!window.Tmapv2 || !window.Tmapv2.Map) {
          throw new Error('Tmap SDK 로드 후에도 Tmapv2.Map 이 없습니다.');
        }
        return window.Tmapv2;
      })
      .catch(err => {
        sdkPromise = null; // 실패는 캐시하지 않는다
        throw err;
      });
    return sdkPromise;
  }

  /** 좌표 배열 전체가 보이도록 지도 범위를 맞춘다. */
  function fitBounds(Tmapv2, map, points) {
    if (points.length === 1) return map.setCenter(points[0]);
    const bounds = new Tmapv2.LatLngBounds();
    points.forEach(p => bounds.extend(p));
    map.fitBounds(bounds);
  }

  /**
   * 지도 인스턴스를 컨테이너째 재사용한다.
   * 카드를 넘길 때마다 new Tmapv2.Map 을 만들면 타일을 처음부터 다시 받아 눈에 띄게 끊긴다.
   * 지도는 한 번만 만들고 마커/경로만 지웠다 다시 그린다.
   */
  let live = null; // { host, map, markers[], polylines[] }

  function clearOverlays() {
    if (!live) return;
    live.markers.forEach(m => m.setMap(null));
    live.polylines.forEach(p => p.setMap(null));
    live.markers = [];
    live.polylines = [];
  }

  /** 경로가 같으면 fitBounds 를 건너뛴다 — 같은 범위로 계속 다시 맞추면 화면이 튄다. */
  let lastFitKey = '';

  /**
   * 컨테이너에 지도를 그린다.
   *   origin   : {lat, lng} 출발지
   *   hospitals: [{id, name, lat, lng, rank, best, raw}] — raw.route 에 경로/혼잡도
   *   activeId : 경로를 그릴 병원 id
   */
  async function render(container, { origin, hospitals = [], activeId = '', onSelect = null } = {}) {
    if (!container || !origin) return;
    const Tmapv2 = await loadSdk();

    // 지도는 한 번만 만든다. 컨테이너가 갈렸을 때(화면 재렌더)만 새로 만든다.
    let created = false;
    if (!live || live.host !== container || !container.isConnected) {
      created = true;
      container.innerHTML = '';
      live = {
        host: container,
        map: new Tmapv2.Map(container, {
          center: new Tmapv2.LatLng(origin.lat, origin.lng),
          width: '100%',
          height: '100%',
          zoom: 14,
          zoomControl: false,
          scrollwheel: false
        }),
        markers: [],
        polylines: []
      };
      lastFitKey = '';
    }
    const map = live.map;
    clearOverlays();

    const shown = [new Tmapv2.LatLng(origin.lat, origin.lng)];

    // 출발지
    live.markers.push(new Tmapv2.Marker({
      position: shown[0],
      map,
      title: '현재 위치',
      icon: 'https://tmapapi.tmapmobility.com/upload/tmap/marker/pin_b_m_s.png'
    }));

    for (const h of hospitals) {
      if (!Number.isFinite(h.lat) || !Number.isFinite(h.lng)) continue;
      const position = new Tmapv2.LatLng(h.lat, h.lng);
      shown.push(position);
      const marker = new Tmapv2.Marker({
        position,
        map,
        title: `${h.rank || ''} ${h.name}`.trim(),
        icon: h.id === activeId
          ? 'https://tmapapi.tmapmobility.com/upload/tmap/marker/pin_r_m_s.png'
          : 'https://tmapapi.tmapmobility.com/upload/tmap/marker/pin_g_m_s.png'
      });
      // 마커를 눌러도 그 병원이 선택되게 한다 (카드 스와이프와 같은 동작).
      if (onSelect && h.id !== activeId) {
        try { marker.addListener('click', () => onSelect(h.id)); } catch { /* SDK 버전차 무시 */ }
      }
      live.markers.push(marker);
    }

    // 선택된 병원의 실제 경로를 혼잡도 색으로. 좌표가 없으면(경로 미조회) 생략한다.
    const active = hospitals.find(h => h.id === activeId) || hospitals[0];
    const route = active && active.raw && active.raw.route;
    const path = route && Array.isArray(route.path) ? route.path : [];

    if (path.length >= 2) {
      const latLngs = path.map(([lng, lat]) => new Tmapv2.LatLng(lat, lng));

      // 인덱스별 혼잡도 (겹치면 더 나쁜 값)
      const level = new Array(latLngs.length).fill(0);
      for (const span of route.path_congestion || []) {
        const start = Math.max(0, Math.min(span.start | 0, latLngs.length - 1));
        const end = Math.max(start, Math.min(span.end | 0, latLngs.length - 1));
        for (let i = start; i <= end; i++) level[i] = Math.max(level[i], span.congestion | 0);
      }

      // 같은 혼잡도끼리 이어서 하나의 구간으로 (경계점을 공유해 선이 끊기지 않게)
      const pieces = [];
      let from = 0;
      for (let i = 1; i <= latLngs.length; i++) {
        if (i === latLngs.length || level[i] !== level[from]) {
          const slice = latLngs.slice(from, Math.min(i + 1, latLngs.length));
          if (slice.length >= 2) pieces.push({ path: slice, congestion: level[from] });
          from = i;
        }
      }

      // 흰 테두리를 전부 먼저 깐 뒤 색선을 얹는다. 한 구간씩 번갈아 그리면 다음 구간의
      // 테두리가 앞 구간의 색선을 덮어 경계가 하얗게 끊겨 보인다.
      for (const piece of pieces) {
        live.polylines.push(new Tmapv2.Polyline({
          path: piece.path, strokeColor: '#ffffff', strokeWeight: 12, strokeOpacity: 0.95, map
        }));
      }
      for (const piece of pieces) {
        live.polylines.push(new Tmapv2.Polyline({
          path: piece.path, strokeColor: COLORS[piece.congestion] || COLORS[0], strokeWeight: 7, map
        }));
      }
      latLngs.forEach(p => shown.push(p));
    }

    // 같은 대상이면 범위를 다시 맞추지 않는다 (스와이프 중 화면이 튀는 것을 막는다).
    const fitKey = `${activeId}|${path.length}`;
    if (fitKey !== lastFitKey) {
      fitBounds(Tmapv2, map, shown);
      lastFitKey = fitKey;
      // 갓 만든 지도는 컨테이너 크기를 아직 못 잡은 상태라 fitBounds 가 어긋난다.
      // 한 틱 뒤 같은 범위로 한 번 더 맞춘다.
      if (created) setTimeout(() => { try { fitBounds(Tmapv2, map, shown); } catch { /* 이미 정리됨 */ } }, 300);
    }
    return { hasRoute: path.length >= 2 };
  }

  /** 화면을 떠날 때 인스턴스를 버린다 (컨테이너가 사라지면 재사용할 수 없다). */
  function reset() {
    live = null;
    lastFitKey = '';
  }

  return { render, reset, loadSdk, COLORS };
})();
