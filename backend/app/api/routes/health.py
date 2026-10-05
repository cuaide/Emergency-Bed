"""헬스체크 / 파이프라인 상태."""

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
    """Tmap 지도 SDK용 appKey.

    지도 SDK는 브라우저에서 도니 키가 클라이언트에 노출될 수밖에 없다. 그래도 프론트
    소스에 박아 두지는 않는다 — .env 를 단일 출처로 두어 저장소에 커밋되지 않게 한다.
    운영에 올릴 때는 Tmap 콘솔에서 이 키에 도메인 제한을 걸어 두는 것을 권장한다.
    """
    key = get_settings().tmap_app_key
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TMAP_APP_KEY가 설정되지 않아 지도를 표시할 수 없습니다.",
        )
    return MapConfig(tmap_app_key=key)


@router.get("/places", response_model=PlaceResult, summary="장소명/주소 → 좌표")
def search_place(q: str = Query(min_length=1, description="장소명·주소·건물명")) -> PlaceResult:
    """주소를 좌표로 바꾼다.

    브라우저 위치 권한이 거부된 환경에서도 출발지를 지정할 수 있어야 한다.
    (권한은 사용자가 직접 풀어야 하고, 앱이 우회할 수단이 없다)
    """
    try:
        poi = tmap_service.search_poi(q)
    except tmap_service.TmapQuotaError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        ) from exc
    except tmap_service.TmapError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PlaceResult.model_validate(poi)
