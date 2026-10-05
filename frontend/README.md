# 프론트엔드 (응급 나침반)

빌드 도구 없는 정적 앱이다. 해시 라우팅으로 화면을 전환하고, 데이터 접근은
`window.APP_API` 한 곳에 모여 있다.

## 실행

```bash
# 백엔드 (다른 터미널)
cd backend && uvicorn app.main:app --reload --port 8000

# 프론트엔드
python frontend/serve.py            # http://localhost:5173
```

`serve.py` 가 `/api/*`, `/health`, `/status` 를 8000번으로 프록시하므로 CORS 설정이
필요 없다. 백엔드 없이 데모 데이터만 보려면 `config.js` 에서 `useBackend: false`.

## 파일

| 파일 | 역할 | 수정 빈도 |
|---|---|---|
| `config.js` | 백엔드 주소·조회 옵션·병상 필드 매핑 | 여기만 고치면 되는 경우가 대부분 |
| `adapter.js` | 백엔드 필드명 → 프론트 필드명 변환 | 백엔드 스키마가 바뀔 때 |
| `api.js` | `window.APP_API` — 실제 fetch, 폴백, 타임아웃 | 엔드포인트가 늘 때 |
| `bootstrap.js` | 첫 렌더부터 실서버 데이터가 보이게 함 | 거의 없음 |
| `data.js` | 데모 데이터 + 응급처치 가이드·사설구급차·챗봇 질문 | |
| `prompt.js` | AI 진단 정책·Red Flag 규칙 | |
| `app.js` | 화면 전체. **백엔드 연결 과정에서 수정하지 않았다** | |
| `styles.css` | 스타일 전체 | |
| `serve.py` | 개발용 정적 서버 + API 프록시 | |
| `standalone-demo.html` | 오프라인 데모용 단일 파일 (아래 참고) | |

`index.html` 의 스크립트 로드 순서가 중요하다:
`data.js` → `config.js` → `adapter.js` → `api.js` → `prompt.js` → `app.js` → `bootstrap.js`

## standalone-demo.html — 백엔드에 연결되지 않는다

styles·data·app 을 한 파일에 인라인한 자기완결 버전이다. 외부 요청이 전혀 없어서
`file://` 로 열어도 그대로 동작한다. 서버 없이 화면만 보여줘야 하는 발표·공유용이다.

**연결된 버전이 아니다.** 내부에 예전 데모 `APP_API` 가 들어 있어서 `data.js` 의
병원 5곳만 보여준다. 백엔드 데이터로 확인해야 할 때는 `index.html` 을
`python serve.py` 로 띄워서 봐야 한다.

`app.js` 나 `styles.css` 를 고쳐도 이 파일은 따라 바뀌지 않으므로, 발표 직전에
내용이 최신인지 확인한다. 필요 없으면 지워도 나머지 동작에 영향이 없다.

**추천 규칙만은 예외로 손으로 맞춰 두었다.** 이 파일 안의 `scoreCandidate` 는
백엔드 `recommendation_engine.py` 와 같은 규칙(모드별 가중치, 병상 0·음수 단계
감점, A등급 없으면 Best 미표시)을 오프라인용으로 옮긴 것이다. 규칙이 갈라지면
발표 화면과 실제 서비스가 다른 순위를 보여주므로, 채점을 바꿀 때는 양쪽을 같이
고친다.

또 `file://` 로 열면 브라우저가 위치(Geolocation)를 주지 않는다. 위치 기능까지
보여줘야 하면 이 파일도 `http://localhost` 로 서비스해야 한다.

## sw.js

`app.js` 가 서비스 워커를 등록하려 하지만 저장소에 `sw.js` 가 없다. 등록 실패를
무시하도록 되어 있어(`app.js:456`) 없어도 무해하다. 오프라인 지원이 필요해지면
그때 추가한다.

## 연결 상세

[`docs/frontend-integration.md`](../docs/frontend-integration.md) — 필드 매핑표,
아직 백엔드가 없는 기능, 배포 시 CORS 설정, 문제 해결.
