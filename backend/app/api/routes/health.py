from __future__ import annotations
from fastapi import APIRouter, HTTPException, Response, status
from app import db
from fastapi import Query
from app.config import get_settings
from app.schemas import HealthResponse, MapConfig, PipelineStatus, PlaceResult
from app.services import hospitals as hospital_service
from app.services import tmap as tmap_service

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse, summary="헬스체크")
def health(response: Response) -> HealthResponse:
    db_ok = db.ping()
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        database="up" if db_ok else "down",
    )


@router.get("/status", response_model=PipelineStatus, summary="수집 파이프라인 상태")
def pipeline_status() -> PipelineStatus:
    return PipelineStatus.model_validate(hospital_service.pipeline_status())


@router.get("/config/map", response_model=MapConfig, summary="지도 SDK 설정")
def map_config() -> MapConfig:
    key = get_settings().tmap_app_key
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TMAP_APP_KEY가 설정되지 않아 지도를 표시할 수 없습니다.",
        )
    return MapConfig(tmap_app_key=key)


@router.get("/places", response_model=PlaceResult, summary="장소명/주소 → 좌표")
def search_place(q: str = Query(min_length=1, description="장소명·주소·건물명")) -> PlaceResult:
    try:
        poi = tmap_service.search_poi(q)
    except tmap_service.TmapQuotaError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        ) from exc
    except tmap_service.TmapError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PlaceResult.model_validate(poi)
