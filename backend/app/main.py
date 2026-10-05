"""FastAPI 진입점.

실행:
    cd backend
    python -m uvicorn app.main:app --reload --port 8000

이 API는 공공 데이터를 직접 수집하지 않는다. Timer Trigger → Event Hub →
Event Hub Trigger 파이프라인이 채워 둔 bed_status_latest만 읽는다.

수집 대상이 아닌 외부 서비스는 요청 시점에 부른다 — 출발지가 사용자마다 다른 Tmap,
음성이 요청에만 실려 오는 Speech(STT/TTS), 증상 문장을 넘겨야 하는 Foundry 에이전트가
그렇다. 셋 다 미리 적재할 수 없다는 공통점이 있다.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app import db
from app.api.routes import health, hospitals, recommendations, triage, weather
from app.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.auto_init_db:
        db.init_schema()
    try:
        db.get_pool()
    except Exception:  # noqa: BLE001 - DB가 늦게 떠도 앱은 기동시키고 /health로 노출
        logger.exception("기동 시 PostgreSQL 연결 실패 (요청 시 재시도)")
    yield
    db.close_pool()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="응급 병상 조회 API",
        description=(
            "국립중앙의료원 실시간 가용 병상 데이터를 PostgreSQL(bed_status_latest)에서 조회한다."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        # 증상 분류(/triage)는 POST라 GET만 열어 두면 브라우저에서 막힌다.
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(hospitals.router, prefix=API_PREFIX)
    app.include_router(weather.router, prefix=API_PREFIX)
    app.include_router(triage.router, prefix=API_PREFIX)
    app.include_router(recommendations.router, prefix=API_PREFIX)
    return app

app = create_app()
