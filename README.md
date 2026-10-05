The collector functions keep **API calling/parsing** separate from **DB loading**, so the same code is reused across execution environments.

| Environment | Code |
| --- | --- |
| Local CLI | `rows = fetch_bed_status()` → `save_bed_status(rows)` |
| Timer Trigger | `rows = fetch_bed_status()` → Event Hub output binding |
| Event Hub Trigger | `rows = decode_event_hub_messages(events)` → `save_bed_status(rows)` |

## Collection Scope

**The default is Seoul (서울특별시) only.** This conserves the public API's daily call quota; even if `BED_COLLECT_DISTRICTS` is not set, only Seoul is collected.

```bash
BED_COLLECT_DISTRICTS=서울특별시
```

The full list of 17 provinces/metropolitan cities is in `ALL_DISTRICTS` in `app/config.py`. When expanding nationwide, also check the weather grid cap (`WEATHER_MAX_GRIDS`, default 200). Nationwide coverage grows to 300–400 grids, which gets truncated at the default value.

Weather has no separate scope setting. Grids are derived from the hospital coordinates loaded into `hospitals`, so weather automatically follows the bed collection scope. **This means hospital base information must be loaded first for weather to be collected.** If there are no hospitals, the job does not silently finish with 0 rows; it fails and reports the cause.

## Processing Path by Data Type

| Data | Processing |
| --- | --- |
| Hospital base info | `collect_hospitals_daily` → loaded directly into PostgreSQL (once a day) |
| Real-time available beds | `collect_beds_timer` → Event Hub → `save_beds_eventhub` → PostgreSQL |
| Current weather observations | `collect_weather_current_timer` → loaded directly into PostgreSQL (hourly) |
| Weather forecast | `collect_weather_forecast_timer` → loaded directly into PostgreSQL (8 release times/day) |
| Tmap routes & traffic | No Function. FastAPI calls it at query time, only for top candidates |
| Speech recognition & synthesis (STT/TTS) | No Function. FastAPI calls Azure AI Speech at request time |
| Symptom classification (RAG) | No Function. FastAPI calls an Azure AI Foundry agent |

**Why Tmap is not a Function**: The origin differs for every user, so routes cannot be preloaded. A route only matters at request time and the call quota is finite, so `/hospitals/nearby/routes` calls Tmap only for the top N candidates by straight-line distance (default 5).

**Why weather does not go through Event Hub**: It updates slowly (1 hour / 3 hours) and has only one consumer, so adding a queue would only increase implementation complexity. For presentation purposes as well, the need for Event Hub is best demonstrated by the real-time bed data.

**Why STT/TTS and symptom classification are not Functions either**: Same reason as Tmap. The user's voice and symptom text arrive only with the request, so they cannot be preloaded. All functions in the collection pipeline are batch jobs (Timer/Event Hub); request-response work that keeps the user waiting is handled by FastAPI.

**Why there is no vector store for RAG**: Retrieval and generation are performed by an agent deployed on Azure AI Foundry. This repository has no embedding or indexing code; `services/triage.py` only makes the call and normalizes the response.

## Directory Structure

```
backend/
├── function_app.py              # Azure Functions entry point (v2 model)
├── host.json
├── .env                         # Settings for FastAPI / CLI (git-ignored)
├── local.settings.json          # Settings for Functions (git-ignored)
├── requirements.txt
├── app/
│   ├── config.py                # Environment variables → Settings
│   ├── db.py                    # Connection pool + UPSERT helpers
│   ├── schema.sql               # 3 bed tables + weather_current / weather_forecast
│   ├── eventhub.py              # Serialization/deserialization, direct send
│   ├── grid.py                  # Lat/lon → KMA grid (nx, ny) conversion
│   ├── main.py                  # FastAPI entry point
│   ├── schemas.py               # Response models
│   ├── collectors/
│   │   ├── emergency.py         # Bed & hospital fetch_* / save_*
│   │   └── weather.py           # Current & forecast fetch_* / save_*
│   ├── services/
│   │   ├── hospitals.py         # Queries bed_status_latest
│   │   ├── weather.py           # Queries weather_current / weather_forecast
│   │   ├── tmap.py              # Request-time route lookup (not collection)
│   │   ├── speech.py            # Request-time STT / TTS (not collection)
│   │   └── triage.py            # Request-time Foundry agent call (not collection)
│   └── api/routes/              # health.py, hospitals.py, weather.py, triage.py
├── scripts/                     # init_db.py, collect_beds.py, collect_weather.py
└── tests/
```

## Running Locally

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

docker compose up -d                      # PostgreSQL (localhost:5433)

# Create .env yourself. The command below tells you which keys are needed, and why.
python scripts/check_deploy.py
set -a && source .env && set +a

python -m scripts.init_db                 # Create tables
python -m scripts.collect_beds --dry-run   # Query only, using the default scope (Seoul)
python -m scripts.collect_beds             # Load real-time bed data

# Weather grids are derived from hospital coordinates, so load hospital info first
python -m scripts.collect_beds --hospitals # Load hospital base info (coordinates & grids)
python -m scripts.collect_weather          # Load current weather + forecast

uvicorn app.main:app --reload --port 8000 # http://localhost:8000/docs
```

To run the Functions runtime locally as well:

```bash
func start
```

Functions read `local.settings.json`, while FastAPI and the CLI read `.env` (Core Tools does not read `.env`). The two files share the same keys, so **editing only one of them lets them silently drift apart.** `tests/test_config.py::test_shared_settings_do_not_drift` catches this.

## API

| Method | Path | Description |
| --- | --- | --- |
| GET | `/health` | DB connectivity check (503 on failure) |
| GET | `/status` | Row counts / last update time |
| GET | `/api/v1/hospitals` | Bed list. `sido`, `sigungu`, `q`, `only_available`, `require_ct`, `require_mri`, `require_ventilator`, `include_stale`, `limit`, `offset` |
| GET | `/api/v1/hospitals/nearby` | Radius search around a coordinate. `lat`, `lon`, `radius_km`, `only_available`, `limit` — sorted by straight-line distance |
| GET | `/api/v1/hospitals/nearby/routes` | Attaches Tmap routes to the top N results above and sorts by **actual travel time** |
| GET | `/api/v1/hospitals/summary` | Available beds aggregated by province |
| GET | `/api/v1/hospitals/{hpid}` | Single hospital lookup |
| GET | `/api/v1/hospitals/{hpid}/history` | Bed history (latest 48 records by default) |
| GET | `/api/v1/weather` | Current weather + forecast for a coordinate. `lat`, `lon`, `hours` |
| GET | `/api/v1/weather/hospitals/{hpid}` | Latest current weather at a hospital's location |
| GET | `/api/v1/weather/coverage` | Weather loading status |
| GET | `/api/v1/recommendations` | Up to 3 recommendations with reasoning. `lat`, `lon`, `symptom` or `profile`, `severity`, `radius_km`, `include_detail` |
| POST | `/api/v1/recommendations/intake` | Symptom text → required-resource profile + scoring mode (recommendation step 1) |
| GET | `/api/v1/recommendations/profiles` | List of selectable required-resource profiles |
| POST | `/api/v1/triage/text` | Text symptoms → agent classification (+ guidance audio). `message`, `generate_audio` |
| POST | `/api/v1/triage/voice` | WAV upload → STT → classification → guidance audio. `file`, `generate_audio` |
| GET | `/api/v1/triage/audio/{filename}` | Download synthesized guidance audio |

```bash
curl "http://localhost:8000/api/v1/hospitals?sido=서울특별시&only_available=true&limit=5"
curl "http://localhost:8000/api/v1/hospitals/nearby?lat=37.5665&lon=126.9780&radius_km=5"
curl "http://localhost:8000/api/v1/hospitals/nearby/routes?lat=37.5665&lon=126.9780&limit=5"
curl "http://localhost:8000/api/v1/weather?lat=37.5665&lon=126.9780&hours=12"
```

```bash
curl -X POST "http://localhost:8000/api/v1/triage/text" \
  -H "Content-Type: application/json" \
  -d '{"message": "가슴이 아프고 숨이 차요"}'

curl -X POST "http://localhost:8000/api/v1/triage/voice" \
  -F "file=@symptom.wav" -F "generate_audio=true"
```

`/nearby/routes` returns `503` if `TMAP_APP_KEY` is missing. If the Tmap call fails for an individual candidate, only that candidate is left with `route: null` and pushed to the end of the list, so the overall response still succeeds.

**The structure of the `triage` field in `/triage/*` is determined by the agent.** It is intentionally not fixed in the response model, so the schema doesn't need to be updated every time the prompt changes. If guidance audio synthesis fails, `audio_url` is set to `null` and the classification result is still returned.

## Recommendation Algorithm (`app/recommendation_engine.py`)

Each request goes through 6 stages in order. The `pipeline` field in the response returns the same 6 stages along with the actual numbers, so when results are sparse you can see right on screen where candidates were dropped.

1. **Input structuring** — Symptom text → required-resource profile + scoring mode.
   The Foundry agent is tried first; if it is unavailable or fails, keyword rules are used as a fallback.
   Profiles returned by the agent that are not on the whitelist are ignored.
2. **Initial candidate selection** — Straight-line radius of 5 → 10 → 20 km. Stops once 3 Grade-A hospitals are found.
   The route API is not called here because, while the radius expands, the number of calls would multiply with the number of candidates.
3. **Suitability filter** — If any **required equipment** for the symptom (Y/N items such as CT or ventilator) is 'N', the hospital is excluded regardless of distance. **Bed count is not a reason for exclusion.**
4. **TMAP routing** — Actual distance, ETA, and traffic are attached only to candidates that passed stage 3.
5. **Scoring** — Weights per mode (total 100).

   | | Travel time | Resources (suitability + availability) | Center capability | Freshness | Route & weather |
   | --- | --- | --- | --- | --- | --- |
   | General | 50 | 30 (20+10) | 10 | 7 | 3 |
   | Severe | 30 | 40 (25+15) | 20 | 7 | 3 |

6. **Top 3 · Best** — Grade A is prioritized, and only the #1 Grade-A hospital gets the `Best` label.
   **If there is no Grade-A hospital, no Best is assigned at all** (`has_best: false`).
   Unsuitable hospitals are never added just to fill out the count.

### Handling Zero / Negative Bed Counts

The collected data contains many zeros and negative values. Negative values appear to represent **the number of people waiting** rather than "no availability," so excluding them all would hide facilities that can still provide care.

| Value | Handling | Grade |
| --- | --- | --- |
| `> 0` | No penalty | A |
| `0` | Minor penalty (availability ×0.85) | A |
| `-1 ~ -5` | Graduated penalty by number waiting (×0.73 ~ ×0.25) | A |
| `-6` or lower | Moved to lower priority (`deprioritized`), not excluded | A |
| `null` / stale update | Grade B + "needs confirmation" notice | B |

The criteria differ in severe mode. If the required treatment resources are available, a hospital is not deprioritized even when beds are zero or negative; it only receives a penalty. Waiting lines can shrink, but missing treatment resources won't appear.

"Severe" here is not a confirmed KTAS classification made by medical staff; it is **this service's safety policy for changing the priority of search results.** The response's `mode.disclaimer` contains this same statement, so it must be displayed on screen as-is.

```bash
# Self-check (verifies rules only, without pytest)
python -m app.recommendation_engine --test
# Demo (prints the 6 stages + reasoning report)
python -m app.recommendation_engine
```

## Environment Variables

| Name | Description |
| --- | --- |
| `DATA_GO_KR_SERVICE_KEY` | Public Data Portal **decoded** service key (emergency medical). If the KMA API was approved under a separate key on your account and `WEATHER_API_KEY` is not set, this value is used instead |
| `WEATHER_API_KEY` | KMA short-term forecast service key. **Pasting the encoded key (with `%2F` etc.) as-is is fine; it is decoded automatically.** Falls back to `DATA_GO_KR_SERVICE_KEY` if empty |
| `TMAP_APP_KEY` | Tmap Open API app key. If missing, only `/nearby/routes` returns 503 |
| `WEATHER_CURRENT_SCHEDULE` / `WEATHER_FORECAST_SCHEDULE` | NCRONTAB for weather collection |
| `WEATHER_MAX_GRIDS` | Maximum number of grids to collect (default 200) |
| `WEATHER_FORECAST_HOURS` | How many hours of forecast to store (default 24) |
| `TMAP_MAX_CANDIDATES` / `TMAP_CONCURRENCY` | Number of candidates to route / number of concurrent calls |
| `BED_COLLECT_DISTRICTS` | Provinces to collect (comma-separated, **default: `서울특별시` only**). Both bed and hospital info follow this value |
| `BED_COLLECT_SCHEDULE` | Bed collection NCRONTAB (default `0 */40 * * * *`) |
| `HOSPITAL_COLLECT_SCHEDULE` | Hospital info collection NCRONTAB (default `0 30 18 * * *` = 03:30 KST) |
| `EventHubConnection` | Event Hub connection string **(only this name goes in the binding)** |
| `BED_EVENT_HUB_NAME` / `BED_EVENT_HUB_CONSUMER_GROUP` | Event Hub name / consumer group |
| `PGHOST` `PGPORT` `PGDATABASE` `PGUSER` `PGPASSWORD` `PGSSLMODE` | PostgreSQL connection (use `PGSSLMODE=require` on Azure) |
| `AUTO_INIT_DB` | If `1`, runs schema.sql when FastAPI starts |
| `BED_STALE_AFTER_MINUTES` | Data older than this (in minutes) is marked `is_stale=true` |
| `CORS_ORIGINS` | Allowed origins (comma-separated) |
| `SPEECH_KEY` / `SPEECH_REGION` | Azure AI Speech key / region (default `koreacentral`) |
| `SPEECH_LANGUAGE` / `SPEECH_VOICE` | Recognition language / synthesis voice (defaults `ko-KR`, `ko-KR-SunHiNeural`) |
| `AUDIO_DIR` | Dedicated directory for audio files. Uses a temp directory if empty. **Do not point it at the working directory** |
| `AUDIO_TTL_MINUTES` | Retention time for synthesized audio (default 30; `0` disables cleanup) |
| `PROJECT_ENDPOINT` / `AGENT_NAME` / `AGENT_VERSION` | Foundry project endpoint / agent name / version |

`connection="EventHubConnection"` takes the **name of the environment variable**, not the connection string itself. Values wrapped in `%...%`, such as `%BED_EVENT_HUB_NAME%`, are also resolved as app setting names.

When deploying, do not upload the settings files (`.funcignore` excludes both `.env` and `local.settings.json`); instead, register the same names under the Function App's **Application settings**. `python scripts/check_deploy.py --print-az-command` generates the registration command.

## Schedule Caveats

NCRONTAB has no way to express "every N minutes" exactly when N doesn't divide 60 evenly. `0 */40 * * * *` runs at **:00 and :40** of every hour, so the actual interval alternates between 40 minutes and 20 minutes. If you need even intervals, use a value that divides the hour evenly, such as `0 0,20,40 * * * *` (20 min) or `0 0,30 * * * *` (30 min).

## Design Notes

- **Protecting the latest snapshot**: The UPSERT on `bed_status_latest` includes the condition `WHERE bed_status_latest.hvidate <= EXCLUDED.hvidate`. Event Hub does not guarantee ordering, so a late-arriving older event cannot overwrite a newer value.
- **Safe reprocessing**: `bed_status` uses an UPSERT keyed on the `(hpid, hvidate)` primary key, so duplicate deliveries of the same event (Event Hub is at-least-once) do not add rows.
- **Bad messages**: Messages that fail JSON parsing or are missing `hpid`/`hvidate` are dropped with only a warning log. Throwing an exception would retry the entire batch, leading to infinite reprocessing.
- **Raw data preservation**: The full API response is stored in a `raw` JSONB column, so if new fields are needed later, columns can be added without re-collecting.
- **Column-addition migrations**: `CREATE TABLE IF NOT EXISTS` does not alter columns of an existing table. So columns added later, such as `hv1`–`hv12` and `hospitals.nx/ny`, are applied separately with `ADD COLUMN IF NOT EXISTS`. `schema.sql` is safe to run any number of times.
- **FastAPI does not collect public data directly**: It only queries the loaded tables. However, external services that cannot be preloaded are called at request time: Tmap (origin), Speech (voice), and the Foundry agent (symptom text). All three depend on user input and cannot be turned into batch jobs.
- **The service layer does not throw HTTPException**: It throws only domain exceptions such as `TmapError` / `SpeechError` / `TriageAgentError`, and conversion to HTTP status codes is handled exclusively by `api/routes/`. This lets the same code be used from Functions or the CLI.
- **The voice routes are intentionally synchronous (def)**: The Speech SDK and agent calls are blocking calls that take several seconds; calling them directly inside `async def` would stall the event loop and block other requests. Keeping them synchronous lets FastAPI offload them to a thread pool.
- **Audio downloads validate the filename**: If `/triage/audio/{filename}` accepted the path as-is, `local.settings.json` and `.env` in the working directory could be served. Both the name pattern and the actual location are checked, and files are kept only under `AUDIO_DIR`.

## Tests

```bash
python -m pytest tests -q                 # Integration tests are skipped if no DB is available
PGHOST=localhost PGPORT=5432
PGPASSWORD=1234 python -m pytest tests -q
```