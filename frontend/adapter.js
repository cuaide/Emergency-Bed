
window.APP_ADAPTER = (() => {
  const cfg = () => window.APP_CONFIG || {};

 
  function num(value) {
    if (value === null || value === undefined || value === '') return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function bool(value) {
    if (value === true || value === false) return value;
    if (value === 'Y' || value === 'y' || value === 1 || value === '1') return true;
    if (value === 'N' || value === 'n' || value === 0 || value === '0') return false;
    return null;
  }

  const UNKNOWN_MINUTES = 999;
  const MAX_PLAUSIBLE_MINUTES = 7 * 24 * 60;

  function minutesSince(value, now = Date.now()) {
    if (!value) return UNKNOWN_MINUTES;
    const t = new Date(value).getTime();
    if (!Number.isFinite(t)) return UNKNOWN_MINUTES;
    const minutes = Math.max(0, Math.round((now - t) / 60000));
    return minutes > MAX_PLAUSIBLE_MINUTES ? UNKNOWN_MINUTES : minutes;
  }
  function shortLabel(row) {
    const addr = row.duty_addr || '';
    const tail = addr.split(' ').filter(Boolean).slice(-2).join(' ');
    const district = row.sigungu || row.sido || '';
    return [district, tail].filter(Boolean).join(' · ') || (row.duty_name || '');
  }

  function trafficLabel(route) {
    if (!route) return '정보 없음';
    const meters = num(route.distance_m);
    const seconds = num(route.duration_s);
    if (!meters || !seconds) return '정보 없음';
    const kmh = (meters / 1000) / (seconds / 3600);
    if (kmh < 15) return '혼잡';
    if (kmh < 30) return '보통';
    return '원활';
  }

  function etaMinutes(route, distanceKm) {
    const fromRoute = route ? num(route.duration_min) : null;
    if (fromRoute !== null) return Math.max(1, Math.round(fromRoute));
    const km = num(distanceKm);
    if (km === null) return 999;
    return Math.max(1, Math.round((km / 25) * 60));
  }

  function project(lat, lng, center, spanKm, clampEdges = true) {
    const halfSpan = Math.max(1, spanKm) / 2;
    const kmPerLat = 111.32;
    const kmPerLng = 111.32 * Math.cos((center.lat * Math.PI) / 180);

    const dxKm = (lng - center.lng) * kmPerLng;
    const dyKm = (lat - center.lat) * kmPerLat;


    const x = 50 + (dxKm / halfSpan) * 40;
    const y = 50 - (dyKm / halfSpan) * 40; 
    if (!clampEdges) return { x, y };
    const clamp = v => Math.min(94, Math.max(6, v));
    return { x: clamp(x), y: clamp(y) };
  }

  function mapPosition(row, center, spanKm) {
    const lat = num(row.latitude);
    const lng = num(row.longitude);
    if (lat === null || lng === null || !center) return { x: 50, y: 50 };
    return project(lat, lng, center, spanKm);
  }


  function rainRiskFromMm(mm) {
    const v = num(mm) || 0;
    if (v >= 5) return 2;
    if (v > 0) return 1;
    return 0;
  }


  function beds(row) {
    const fields = cfg().bedFields || {};
    const out = {};
    for (const [key, field] of Object.entries(fields)) {
      out[key] = field ? num(row[field]) : null;
    }
    return out;
  }

  function normalizeHospital(row, options = {}) {
    const { center = null, spanKm = 20, rainRisk = 0, now = Date.now() } = options;
    const route = row.route || null;
    const distanceKm = num(route && route.distance_km) ?? num(row.distance_km) ?? 999;
    const { x, y } = mapPosition(row, center, spanKm);

    return {
      id: row.hpid,
      name: row.duty_name || row.hpid,
      address: row.duty_addr || '',
      short: shortLabel(row),
      phone: row.duty_tel3 || '',
      centerType: row.duty_emcls_name || '응급의료기관',
      lat: num(row.latitude),
      lng: num(row.longitude),
      distanceKm,
      eta: etaMinutes(route, distanceKm),
      traffic: trafficLabel(route),
      congestionLabel: (route && route.congestion_label) || null,
      updatedMinutes: minutesSince(row.hvidate, now),
      x,
      y,
      beds: beds(row),
      equipment: {
        CT: bool(row.hvctayn),
        MRI: bool(row.hvmriayn),
        angiography: bool(row.hvangioayn),
        ventilator: bool(row.hvventiayn)
      },
      rainRisk,
      raw: row
    };
  }

  function normalizeHospitals(rows, options = {}) {
    if (!Array.isArray(rows)) return [];
    return rows
      .filter(row => row && row.hpid)
      .map(row => normalizeHospital(row, options))
      .sort((a, b) => a.eta - b.eta || a.distanceKm - b.distanceKm);
  }

  function weatherIconKey(mm, label) {
    if (mm > 0 || /비|소나기/.test(label)) return 'rain';
    if (/눈/.test(label)) return 'snow';
    if (/구름|흐림/.test(label)) return 'cloud';
    return 'clear';
  }

  function normalizeWeather(payload) {
    const current = payload && payload.current;
    if (!current) return null;
    const mm = num(current.rn1) || 0;


    const pty = num(current.pty);
    const sky = (payload.forecast || []).find(p => p && p.sky_label)?.sky_label;
    const precipitating = mm > 0 || (pty !== null && pty > 0);
    const label = precipitating ? (current.pty_label || '비') : (sky || '맑음');
    return {
      type: label,
      label,
      summary: mm > 0 ? `${label} 오는 날` : label,
      amountMm: mm,
      temperature: num(current.t1h),
      humidity: num(current.reh),
      observedAt: current.base_datetime || null,
      rainRisk: rainRiskFromMm(mm),
      icon: weatherIconKey(mm, label)
    };
  }

  function bedSignal(raw) {
    const signal = raw || {};
    const queued = signal.queued_beds || [];
    const zero = signal.zero_beds || [];
    const unknown = signal.unknown_beds || [];
    const queue = num(signal.queue_depth);

    let level = 'ok';
    let label = '';
    if (queue !== null && queue > 0) {
      level = signal.deprioritized ? 'backlog' : 'queued';
      label = `대기 ${queue}명 추정`;
    } else if (zero.length) {
      level = 'zero';
      label = `${zero[0]} 가용 0`;
    } else if (unknown.length) {
      level = 'unknown';
      label = `${unknown[0]} 정보 없음`;
    }

    return {
      level,
      label,
      queueDepth: queue,
      zero,
      unknown,
      queued: queued.map(q => ({ label: q.label, queue: num(q.queue) })),
      deprioritized: signal.deprioritized === true,

      penalty: num(signal.penalty) || 0
    };
  }

  function normalizeMode(payload) {
    const mode = payload && payload.mode;
    if (!mode) return null;
    return {
      key: mode.key || 'normal',
      label: mode.label || '',
      severe: mode.severe === true,
      resourceWeight: num(mode.resource_weight) || 0,
      disclaimer: mode.disclaimer || '',
      weights: (mode.weights || []).map(w => ({
        key: w.key,
        label: w.label,
        weight: num(w.weight) || 0
      }))
    };
  }


  function normalizePipeline(payload) {
    return ((payload && payload.pipeline) || []).map(s => ({
      step: num(s.step) || 0,
      key: s.key,
      label: s.label || '',
      detail: s.detail || '',
      value: s.value
    }));
  }

  function normalizeRecommendation(item) {
    const tile = key => (item.tiles || []).find(t => t.key === key) || {};
    const grade = String(item.grade || '').startsWith('A') ? 'A' : 'B';
    const distanceKm = num(item.route_distance_km) ?? num(item.distance_km) ?? 999;
    const eta = num(item.eta_min);
    // 이름을 beds 로 두면 아래 beds(item) 매핑 함수를 가려 버린다.
    const bedState = bedSignal(item.bed_signal);

    return {
      id: item.hpid,
      name: item.hospital_name || item.hpid,
      address: item.address || '',
      
      short: (item.address || '').split(' ').filter(Boolean).slice(1, 3).join(' '),
      phone: item.phone || '',
      centerType: tile('center').value || '응급의료기관',
      lat: num(item.latitude),
      lng: num(item.longitude),
      distanceKm,
      
      eta: eta !== null ? Math.max(1, Math.round(eta)) : Math.max(1, Math.round((distanceKm / 25) * 60)),
      traffic: trafficLabel(item.route),
      updatedMinutes: num(item.updated_minutes) ?? 999,
      grade,
      best: item.badge === 'Best',
      badge: item.badge || null,
  
      confirmRequired: item.confirm_required === true,

      deprioritized: item.deprioritized === true,
      bedSignal: bedState,
      rank: num(item.rank) ?? 0,
      total: num(item.total_score) ?? 0,
      status: grade === 'A' ? (bedState.label || '가용 정보 확인') : '전화 확인 필요',
      reasons: (item.reasons || []).map(r => r.text).filter(Boolean),
      summary: item.summary || '',
      warnings: item.warnings || [],
      scoreBreakdown: item.score_breakdown || [],
      evidence: item.evidence || null,
      isStale: item.is_stale === true,
      raw: { hpid: item.hpid, route: item.route || null },
      beds: beds(item)
    };
  }

  return {
    normalizeHospital,
    normalizeHospitals,
    normalizeRecommendation,
    normalizeMode,
    normalizePipeline,
    normalizeWeather,
    rainRiskFromMm,
    minutesSince,
    mapPosition,
    trafficLabel,
    etaMinutes
  };
})();
