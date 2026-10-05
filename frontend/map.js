
window.APP_MAP = (() => {
  const SDK_URL = 'https://apis.openapi.sk.com/tmap/jsv2?version=1&appKey=';

  const COLORS = { 0: '#9aa0b4', 1: '#22a06b', 2: '#e2a63b', 3: '#e4762f', 4: '#d63b3b' };

  let sdkPromise = null;

  function appendScript(src) {
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = src;
      script.async = false; 
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
        sdkPromise = null; 
        throw err;
      });
    return sdkPromise;
  }

  function fitBounds(Tmapv2, map, points) {
    if (points.length === 1) return map.setCenter(points[0]);
    const bounds = new Tmapv2.LatLngBounds();
    points.forEach(p => bounds.extend(p));
    map.fitBounds(bounds);
  }

  let live = null; 

  function clearOverlays() {
    if (!live) return;
    live.markers.forEach(m => m.setMap(null));
    live.polylines.forEach(p => p.setMap(null));
    live.markers = [];
    live.polylines = [];
  }

  let lastFitKey = '';

  async function render(container, { origin, hospitals = [], activeId = '', onSelect = null } = {}) {
    if (!container || !origin) return;
    const Tmapv2 = await loadSdk();

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

      if (onSelect && h.id !== activeId) {
        try { marker.addListener('click', () => onSelect(h.id)); } catch { /* SDK 버전차 무시 */ }
      }
      live.markers.push(marker);
    }

    const active = hospitals.find(h => h.id === activeId) || hospitals[0];
    const route = active && active.raw && active.raw.route;
    const path = route && Array.isArray(route.path) ? route.path : [];

    if (path.length >= 2) {
      const latLngs = path.map(([lng, lat]) => new Tmapv2.LatLng(lat, lng));


      const level = new Array(latLngs.length).fill(0);
      for (const span of route.path_congestion || []) {
        const start = Math.max(0, Math.min(span.start | 0, latLngs.length - 1));
        const end = Math.max(start, Math.min(span.end | 0, latLngs.length - 1));
        for (let i = start; i <= end; i++) level[i] = Math.max(level[i], span.congestion | 0);
      }


      const pieces = [];
      let from = 0;
      for (let i = 1; i <= latLngs.length; i++) {
        if (i === latLngs.length || level[i] !== level[from]) {
          const slice = latLngs.slice(from, Math.min(i + 1, latLngs.length));
          if (slice.length >= 2) pieces.push({ path: slice, congestion: level[from] });
          from = i;
        }
      }

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


    const fitKey = `${activeId}|${path.length}`;
    if (fitKey !== lastFitKey) {
      fitBounds(Tmapv2, map, shown);
      lastFitKey = fitKey;
      if (created) setTimeout(() => { try { fitBounds(Tmapv2, map, shown); } catch { /* 이미 정리됨 */ } }, 300);
    }
    return { hasRoute: path.length >= 2 };
  }

  function reset() {
    live = null;
    lastFitKey = '';
  }

  return { render, reset, loadSdk, COLORS };
})();
