
window.APP_API = (() => {
  const cfg = () => window.APP_CONFIG || {};
  const adapter = () => window.APP_ADAPTER;

  let searchCenter = null;


  let lastSource = null;


  let mapConfigPromise = null;


  let routeBlockedReason = null;

  function currentCenter() {
    if (searchCenter) return searchCenter;

    return null;
  }

  function url(path, params) {
    const base = (cfg().baseUrl || '').replace(/\/$/, '');
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params || {})) {
      if (value !== undefined && value !== null) query.set(key, String(value));
    }
    const qs = query.toString();
    return `${base}${path}${qs ? `?${qs}` : ''}`;
  }


  function liveUrl(path) {
    const base = cfg().baseUrl || cfg().liveBaseUrl || '';
    return `${base.replace(/\/$/, '')}${path}`;
  }


  async function getJson(path, params, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs || cfg().timeoutMs || 6000);
    try {
      const res = await fetch(url(path, params), {
        method: 'GET',
        headers: { Accept: 'application/json' },
        signal: controller.signal
      });
      if (!res.ok) {
        const error = new Error(`${res.status} ${res.statusText}`);
        error.status = res.status;
        throw error;
      }
      return await res.json();
    } finally {
      clearTimeout(timer);
    }
  }

  async function postJson(path, payload, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs || cfg().timeoutMs || 6000);
    try {
      const res = await fetch(url(path), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(payload || {}),
        signal: controller.signal
      });
      if (!res.ok) {
        const error = new Error(`${res.status} ${res.statusText}`);
        error.status = res.status;
        throw error;
      }
      return await res.json();
    } finally {
      clearTimeout(timer);
    }
  }


  const warned = new Set();
  function warnOnce(key, message) {
    if (warned.has(key)) return;
    warned.add(key);
    console.warn(`[APP_API] ${message}`);
  }

  function clone(value) {
    return typeof structuredClone === 'function'
      ? structuredClone(value)
      : JSON.parse(JSON.stringify(value));
  }


  const DEMO_HOSPITALS = clone((window.APP_DATA && window.APP_DATA.hospitals) || []);

  function demoHospitals() {
    return clone(DEMO_HOSPITALS);
  }


  function setCurrentWeather(weather) {
    if (window.APP_DATA) window.APP_DATA.currentWeather = weather;
  }

  async function fetchRainRisk(center) {
    if (!cfg().useWeather || !center) return 0;
    try {
      const payload = await getJson('/api/v1/weather', { lat: center.lat, lon: center.lng, hours: 1 });

      const weather = adapter().normalizeWeather(payload);
      setCurrentWeather(weather);
      if (!weather) {
        warnOnce('weather-empty', '해당 지역의 기상 실황이 없어 날씨 표시를 생략합니다.');
        return 0;
      }
      return weather.rainRisk;
    } catch (err) {
      warnOnce('weather', `날씨 조회 실패 (rainRisk 0 으로 진행): ${err.message}`);
      setCurrentWeather(null);
      return 0;
    }
  }


  async function fetchBedRows(center) {
    const common = {
      lat: center.lat,
      lon: center.lng,
      only_available: !cfg().includeUnavailable,
      include_stale: cfg().includeStale !== false
    };

    const pool = await getJson('/api/v1/hospitals/nearby', {
      ...common,
      radius_km: cfg().poolRadiusKm || 40,
      limit: cfg().poolLimit || 60
    });

    if (!cfg().useRoutes) return pool;

    try {
      const routed = await getJson(
        '/api/v1/hospitals/nearby/routes',
        { ...common, radius_km: cfg().radiusKm || 20, limit: cfg().routeLimit || 5 },
        cfg().routeTimeoutMs || 12000
      );
      if (Array.isArray(routed) && routed.length) {
        const byId = new Map(pool.map(row => [row.hpid, row]));
        for (const row of routed) byId.set(row.hpid, row); // 경로가 붙은 쪽을 우선
        return [...byId.values()];
      }
      warnOnce('routes-empty', '경로 조회 결과가 비어 직선거리 추정으로 진행합니다.');
    } catch (err) {
      if (err.status === 503) {
        warnOnce('tmap', '백엔드에 TMAP_APP_KEY 가 없어 직선거리로 진행합니다.');
      } else if (err.status === 429) {
        routeBlockedReason = 'Tmap 경로 호출 한도를 초과했습니다. 이동시간은 직선거리 추정값입니다.';
        warnOnce('quota', routeBlockedReason);
      } else {
        warnOnce('routes', `경로 조회 실패, 직선거리로 진행합니다: ${err.message}`);
      }
    }
    return pool;
  }

  return {

    async getCurrentLocation() {
      const position = await new Promise((resolve, reject) => {
        if (!navigator.geolocation) return reject(new Error('UNSUPPORTED'));
        navigator.geolocation.getCurrentPosition(resolve, reject, {
          enableHighAccuracy: true,
          timeout: 6000,
          maximumAge: 60000
        });
      });

      const location = {
        lat: position.coords.latitude,
        lng: position.coords.longitude,
        accuracy: `약 ${Math.round(position.coords.accuracy)}m`,

        accuracyMeters: Math.round(position.coords.accuracy),
        address: '현재 GPS 위치',
        short: '현재 GPS 위치'
      };
      searchCenter = { lat: location.lat, lng: location.lng };

      if (cfg().useBackend && cfg().refreshHospitalsOnLocation) {

        try {
          const rows = await window.APP_API.fetchHospitals();
          if (Array.isArray(rows) && rows.length) window.APP_DATA.hospitals = rows;
        } catch (err) {
          warnOnce('refresh-on-location', `위치 확인 후 병원 갱신 실패: ${err.message}`);
        }
      }
      return location;
    },

    async fetchHospitals(options = {}) {
      if (!cfg().useBackend) {
        lastSource = 'demo';
        return demoHospitals();
      }

      const center = options.center || currentCenter();
      if (!center || !Number.isFinite(center.lat) || !Number.isFinite(center.lng)) {
        throw new Error('현재 위치 또는 주소로 출발지를 먼저 설정해 주세요.');
      }

      try {
        const [rows, rainRisk] = await Promise.all([fetchBedRows(center), fetchRainRisk(center)]);
        const normalized = adapter().normalizeHospitals(rows, {
          center,
          spanKm: cfg().radiusKm || 20,
          rainRisk
        });
        if (normalized.length) {
          lastSource = 'backend';
          return normalized;
        }

        warnOnce('empty', '반경 내 응급실이 없습니다 (백엔드 응답은 정상).');
        lastSource = 'empty';
        return [];
      } catch (err) {
        if (cfg().fallbackToDemo) {
          warnOnce('fallback', `백엔드 조회 실패, data.js 데모 데이터로 대체합니다: ${err.message}`);
          lastSource = 'demo';
          return demoHospitals();
        }
        throw err;
      }
    },

    async fetchRoutesFor(ids) {
      const list = (ids || []).filter(Boolean).slice(0, 5);
      const center = currentCenter();
      if (!list.length || !center || !cfg().useBackend || !cfg().useRoutes) return [];
      try {
        return await getJson(
          '/api/v1/hospitals/routes',
          { lat: center.lat, lon: center.lng, hpids: list.join(',') },
          cfg().routeTimeoutMs || 12000
        );
      } catch (err) {
        if (err.status === 429) {
          routeBlockedReason = 'Tmap 경로 호출 한도를 초과했습니다. 이동시간은 직선거리 추정값입니다.';
          warnOnce('quota', routeBlockedReason);
        } else {
          warnOnce('routes-for', `상위 후보 경로 조회 실패: ${err.message}`);
        }
        return [];
      }
    },

    routeBlockedReason() {
      return routeBlockedReason;
    },

    async fetchRecommendations({
      detail = false,
      profile = null,
      radiusKm = null,
      symptom = null,
      severity = 'auto'
    } = {}) {
      const center = currentCenter();
      if (!center) throw new Error('검색 기준 좌표가 없습니다.');
      return getJson(
        '/api/v1/recommendations',
        {
          lat: center.lat,
          lon: center.lng,
          include_detail: detail,
          profile: profile || undefined,
          symptom: symptom || undefined,
          severity: severity || undefined,
          radius_km: radiusKm || undefined
        },
        cfg().routeTimeoutMs || 12000
      );
    },

    async structureInput(message, { severity = 'auto' } = {}) {
      if (!cfg().useBackend) return null;
      try {
        return await postJson(
          '/api/v1/recommendations/intake',
          { message: String(message || ''), severity },
          cfg().aiTimeoutMs || 35000
        );
      } catch (err) {
        warnOnce('intake', `증상 구조화 실패, 화면 규칙으로 진행합니다: ${err.message}`);
        return null;
      }
    },


    lastHospitalSource() {
      return lastSource;
    },

    currentSearchCenter() {
      return currentCenter();
    },

    
    setSearchCenter(lat, lng) {
      const latitude = Number(lat);
      const longitude = Number(lng);
      if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return false;
      searchCenter = { lat: latitude, lng: longitude };
      return true;
    },

   
    subscribeBedUpdates(onUpdate) {
      if (!cfg().useBackend || !cfg().useLiveBeds || typeof EventSource !== 'function') {
        return () => {};
      }
      const source = new EventSource(liveUrl('/api/v1/hospitals/stream'));
      source.onmessage = event => {
        try {
          const rows = JSON.parse(event.data);
          if (Array.isArray(rows) && rows.length) onUpdate(rows);
        } catch (err) {
          warnOnce('live-parse', `실시간 병상 갱신 파싱 실패: ${err.message}`);
        }
      };
      source.onerror = () => warnOnce('live', '실시간 병상 갱신 연결이 불안정합니다 (자동 재연결 시도 중).');
      return () => source.close();
    },

    
    async checkHealth() {
      return getJson('/health');
    },

    
    async searchPlace(keyword) {
      const q = String(keyword || '').trim();
      if (!q) throw new Error('검색어가 비어 있습니다.');
      return getJson('/places', { q });
    },

    
    async mapConfig() {
      if (!mapConfigPromise) {
        mapConfigPromise = getJson('/config/map').catch(err => {
          mapConfigPromise = null; 
          throw err;
        });
      }
      return mapConfigPromise;
    },

    
    async createTransportRequest(payload) {
      const path = cfg().endpoints && cfg().endpoints.transportRequest;
      if (!path) {
        warnOnce('transport', '이송 요청 엔드포인트가 없어 로컬 응답으로 처리합니다.');
        return { ok: true, pending: true, requestId: `LOCAL-${Date.now()}`, ...payload };
      }
      try {
        return await postJson(path, payload);
      } catch (err) {
        console.error('[APP_API] 이송 요청 전송 실패', err);
        return { ok: false, error: err.message, requestId: `LOCAL-${Date.now()}`, ...payload };
      }
    },

    
    async sendAIGuidance(message) {
      const path = cfg().endpoints && cfg().endpoints.aiGuidance;
      if (!path) {
        warnOnce('ai', 'AI 안내 엔드포인트가 없어 로컬 처리합니다.');
        return { ok: true, pending: true, triage: null, audioUrl: null };
      }
      try {
        const result = await postJson(path, { message, generate_audio: true }, cfg().aiTimeoutMs || 20000);
        return {
          ok: true,
          triage: result.triage,
          audioUrl: result.audio_url ? url(result.audio_url) : null
        };
      } catch (err) {
        console.error('[APP_API] AI 안내 전송 실패', err);
        return { ok: false, error: err.message };
      }
    },

    async saveAuditLog(payload) {
      const path = cfg().endpoints && cfg().endpoints.auditLog;
      if (!path) {
        warnOnce('audit', '감사 로그 엔드포인트가 없어 전송을 건너뜁니다.');
        return { ok: true, pending: true };
      }
      try {
        return await postJson(path, payload);
      } catch (err) {
        console.error('[APP_API] 감사 로그 전송 실패', err);
        return { ok: false, error: err.message };
      }
    }
  };
})();
