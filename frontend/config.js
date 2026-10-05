

window.APP_CONFIG = {

  baseUrl: '',
  useBackend: true,
  fallbackToDemo: false,
  timeoutMs: 6000,
  routeTimeoutMs: 12000,
  aiTimeoutMs: 35000,

  radiusKm: 20,
  limit: 20,
  poolRadiusKm: 40,
  poolLimit: 60,


  useRoutes: true,
  routeLimit: 5,
  includeUnavailable: true,
  includeStale: true,
  useWeather: true,
  useLiveBeds: true,
  liveBaseUrl: 'http://localhost:8000',
  refreshHospitalsOnLocation: true,


  bedFields: {
    er: 'hvec',            // 응급실 일반 병상
    operating: 'hvoc',     // 수술실
    general: 'hvicc',      // 일반 중환자실
    neurosurgery: 'hvcc',  // 신경외과 중환자실
    thoracic: 'hvccc',     // 흉부외과 중환자실
    internal: 'hv2',       // 내과 중환자실
    surgical: 'hv3',       // 외과 중환자실
    neurology: 'hv5',      // 신경과 입원실
    burn: 'hv8',           // 화상 중환자실
    trauma: 'hv9',         // 외상 중환자실
    poison: 'hv7'          // 약물중독 중환자실
  },

  endpoints: {
    transportRequest: null,
    aiGuidance: '/api/v1/triage/text',
    auditLog: null
  }
};
