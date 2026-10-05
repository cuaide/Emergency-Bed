
/**
 * 프론트엔드 ↔ 백엔드 연결 설정. 코드를 고칠 일 없이 이 파일만 바꾸면 된다.
 *
 * app.js / data.js 는 손대지 않는다. 연결은 api.js 한 곳에서만 일어나고,
 * api.js 가 이 설정과 adapter.js 를 참조한다.
 */
window.APP_CONFIG = {
  /**
   * 백엔드 주소.
   *  - ''        : 같은 출처(same-origin). serve.py 로 띄우면 /api 를 8000번으로
   *                프록시하므로 CORS 설정이 아예 필요 없다. 권장 개발 방식.
   *  - 'http://localhost:8000' : 정적 파일과 백엔드를 따로 띄운 경우.
   *                이때는 백엔드에 CORS_ORIGINS 를 설정해야 한다.
   *  - 'https://<앱이름>.azurewebsites.net' : 배포된 백엔드.
   */
  // 백엔드 포트는 8000 하나로 통일한다 (serve.py 의 기본 프록시 대상과 같다).
  // 한때 8010/8001 로 옮겨 다녔는데, serve.py 는 8000 을 계속 보고 있어서 추천·경로·
  // 지도 라우트가 전부 404 로 죽었다. 포트를 옮기려면 serve.py --backend 도 같이 바꾼다.
  //
  // 주의: 8000 에서 uvicorn 을 Ctrl-C 가 아닌 방법으로 끊으면 워커가 고아로 남아
  // 소켓을 계속 물고 옛 코드로 응답한다(2026-08-09 실제 발생). /health 는 200 이라
  // 멀쩡해 보이고 새 라우트만 404 가 나므로, 증상이 이러면 8000 리스너부터 확인한다.
  //
  // serve.py의 same-origin 프록시를 사용한다. 프론트가 특정 백엔드 포트에 직접
  // 붙으면 실행 포트가 달라지는 순간 지도 설정과 경로 API가 모두 끊어진다.
  baseUrl: '',

  /** false 로 두면 백엔드를 호출하지 않고 data.js 데모 데이터만 쓴다. */
  useBackend: true,

  /**
   * 백엔드가 죽어 있거나 응답이 비었을 때 data.js 데모 데이터로 되돌릴지.
   * 시연 중 백엔드 장애로 화면이 비는 것을 막아 준다.
   * 실제 운영에서는 false 로 두어 "낡은 데이터를 진짜처럼 보여주는" 상황을 피한다.
   */
  // 실제 지도/경로 화면에서 API 실패를 mock 병원 좌표로 덮지 않는다.
  // 실패 원인을 화면에 드러내야 엉뚱한 출발지로 경로가 그려지는 일을 막을 수 있다.
  fallbackToDemo: false,

  /** fetch 타임아웃(ms). Tmap 경로 조회가 붙는 경로는 조금 더 길게 잡는다. */
  timeoutMs: 6000,
  routeTimeoutMs: 12000,
  /** 증상 분류(Foundry 에이전트) 호출은 실측 20~22초 걸려 기본값보다 길게 잡는다. */
  aiTimeoutMs: 35000,

  /** 지도 마커 좌표 계산에 쓰는 기준 반경(km). 넓히면 마커가 가운데로 몰린다. */
  radiusKm: 20,
  limit: 20,

  /**
   * 후보 풀 반경/개수. 화상·외상처럼 특정 병상을 갖춘 병원은 멀리 있을 수 있어
   * (예: 화상 → 한림대한강성심병원, 강남에서 거리순 32위) 넓게 받아 둔다.
   * app.js 가 5→10→20→40km 로 스스로 좁혀 쓰므로 많이 받아도 가까운 곳이 먼저 걸린다.
   */
  poolRadiusKm: 40,
  poolLimit: 60,

  /**
   * Tmap 실제 이동시간을 쓸지. true 면 /hospitals/nearby/routes 를 먼저 호출한다.
   * 백엔드에 TMAP_APP_KEY 가 없으면 503 이 오고, 자동으로 /hospitals/nearby 로 내려간다.
   */
  useRoutes: true,
  /** 경로를 조회할 후보 수. Tmap 호출 쿼터를 아끼려 백엔드가 1~10 으로 제한한다. */
  routeLimit: 5,

  /** 가용 병상 0인 곳도 받아올지. app.js 가 자체 점수로 걸러내므로 기본 true. */
  includeUnavailable: true,
  /** 갱신 지연(stale) 데이터 포함 여부. app.js 가 '갱신 N분 전'으로 표시해 준다. */
  includeStale: true,

  /** 날씨(강수)를 조회해 rainRisk / DATA.currentWeather 에 반영할지. */
  useWeather: true,

  /**
   * 병상 실시간 갱신(Server-Sent Events)을 켤지. false면 다시 찾기를 눌러야
   * 최신 병상 수를 본다.
   */
  useLiveBeds: true,

  /**
   * SSE 스트림 전용 주소. serve.py 프록시는 응답을 통째로 읽은 뒤 돌려주므로
   * 끝나지 않는 스트림은 통과하지 못한다. baseUrl이 비어 있는 개발 모드에서는
   * 이 값으로 백엔드에 직접 붙는다. baseUrl이 채워진 배포 환경에서는 baseUrl을
   * 그대로 쓰고 이 값은 무시한다.
   */
  liveBaseUrl: 'http://localhost:8000',

  /** 위치 동의 직후 병원 목록을 그 좌표로 다시 불러올지. */
  refreshHospitalsOnLocation: true,

  /**
   * 병상 항목 매핑: 프론트 beds.* ← 백엔드 응답 필드.
   * hv1~hv12 는 공공데이터포털 getEmrrmRltmUsefulSckbdInfoInqire 응답 항목 문서 기준이다.
   *
   * hv5(신경과입원실) / hv7(약물중환자) 는 문서상 병상 수지만 서울 지역 기관은 실제로
   * 'Y'/'N' 만 보고한다(55곳 전수 확인). adapter.js 의 num() 이 'Y' 를 null 로 떨궈
   * '전화 확인 필요'(grade B)로 표시되므로, 숫자로 보고하는 기관이 생기면 자동으로 반영된다.
   */
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
