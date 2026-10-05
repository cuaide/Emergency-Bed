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
    except Exception:  
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
        allow_methods=["GET", "POST"],
        allow_headers=["*"]
    )
    app.include_router(health.router)
    app.include_router(hospitals.router, prefix=API_PREFIX)
    app.include_router(weather.router, prefix=API_PREFIX)
    app.include_router(triage.router, prefix=API_PREFIX)
    app.include_router(recommendations.router, prefix=API_PREFIX)
    return app

app = create_app()
