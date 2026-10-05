from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.schemas import (
    BedHistoryPoint,
    BedStatus,
    BedStatusList,
    BedStatusNearby,
    BedStatusRoute,
    SidoSummary,
)
from app.services import hospitals as hospital_service
from app.services import tmap as tmap_service

_STREAM_POLL_S = 5.0

router = APIRouter(prefix="/hospitals", tags=["hospitals"])


def _attach_routes_or_429(
    rows: list[dict], *, lat: float, lon: float, limit: int
) -> list[dict]:
    try:
        return tmap_service.attach_routes(
            rows, start_latitude=lat, start_longitude=lon, limit=limit
        )
    except tmap_service.TmapQuotaError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        ) from exc


@router.get("", response_model=BedStatusList, summary="응급실 병상 목록 조회")
def list_hospitals(
    sido: str | None = Query(default=None, description="시도명 (예: 서울특별시)"),
    sigungu: str | None = Query(default=None, description="시군구명 (예: 강남구)"),
    q: str | None = Query(default=None, description="기관명/주소 부분 검색어"),
    only_available: bool = Query(default=False, description="응급실 가용 병상이 1자리 이상인 곳만"),
    require_ct: bool = Query(default=False, description="CT 가용 기관만"),
    require_mri: bool = Query(default=False, description="MRI 가용 기관만"),
    require_ventilator: bool = Query(default=False, description="인공호흡기 가용 기관만"),
    include_stale: bool = Query(default=True, description="갱신 지연 데이터 포함 여부"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> BedStatusList:
    rows = hospital_service.list_bed_status(
        sido=sido,
        sigungu=sigungu,
        query=q,
        only_available=only_available,
        require_ct=require_ct,
        require_mri=require_mri,
        require_ventilator=require_ventilator,
        include_stale=include_stale,
        limit=limit,
        offset=offset,
    )
    total = hospital_service.count_bed_status(
        sido=sido,
        sigungu=sigungu,
        query=q,
        only_available=only_available,
        require_ct=require_ct,
        require_mri=require_mri,
        require_ventilator=require_ventilator,
        include_stale=include_stale,
    )
    items = [BedStatus.model_validate(row) for row in rows]
    return BedStatusList(total=total, count=len(items), items=items)


@router.get("/nearby", response_model=list[BedStatusNearby], summary="주변 응급실 조회")
def nearby_hospitals(
    lat: float = Query(description="위도", ge=-90, le=90),
    lon: float = Query(description="경도", ge=-180, le=180),
    radius_km: float = Query(default=10.0, gt=0, le=100),
    only_available: bool = Query(default=True),
    include_stale: bool = Query(default=True),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[BedStatusNearby]:
    rows = hospital_service.find_nearby(
        latitude=lat,
        longitude=lon,
        radius_km=radius_km,
        only_available=only_available,
        include_stale=include_stale,
        limit=limit,
    )
    return [BedStatusNearby.model_validate(row) for row in rows]


@router.get(
    "/nearby/routes",
    response_model=list[BedStatusRoute],
    summary="주변 응급실 + Tmap 실제 이동시간",
)
def nearby_hospitals_with_routes(
    lat: float = Query(description="출발지 위도", ge=-90, le=90),
    lon: float = Query(description="출발지 경도", ge=-180, le=180),
    radius_km: float = Query(default=10.0, gt=0, le=100),
    only_available: bool = Query(default=True),
    include_stale: bool = Query(default=True),
    limit: int = Query(default=5, ge=1, le=10, description="경로를 조회할 후보 수"),
) -> list[BedStatusRoute]:
    if not tmap_service.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TMAP_APP_KEY가 설정되지 않았습니다. /hospitals/nearby 를 사용하세요.",
        )

    candidates = hospital_service.find_nearby(
        latitude=lat,
        longitude=lon,
        radius_km=radius_km,
        only_available=only_available,
        include_stale=include_stale,
        limit=limit,
    )
    if not candidates:
        return []

    enriched = _attach_routes_or_429(candidates, lat=lat, lon=lon, limit=limit)
    return [BedStatusRoute.model_validate(row) for row in enriched]


@router.get(
    "/routes",
    response_model=list[BedStatusRoute],
    summary="지정 기관들의 Tmap 경로",
)
def routes_for_hospitals(
    lat: float = Query(description="출발지 위도", ge=-90, le=90),
    lon: float = Query(description="출발지 경도", ge=-180, le=180),
    hpids: str = Query(description="쉼표로 구분한 hpid (최대 5개)"),
) -> list[BedStatusRoute]:
    if not tmap_service.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TMAP_APP_KEY가 설정되지 않았습니다.",
        )

    ids = [value.strip() for value in hpids.split(",") if value.strip()][:5]
    if not ids:
        return []

    rows = hospital_service.find_nearby(
        latitude=lat,
        longitude=lon,
        radius_km=200,
        only_available=False,
        include_stale=True,
        limit=len(ids),
        hpids=ids,
    )
    if not rows:
        return []

    enriched = _attach_routes_or_429(rows, lat=lat, lon=lon, limit=len(rows))
    return [BedStatusRoute.model_validate(row) for row in enriched]


@router.get("/summary", response_model=list[SidoSummary], summary="시도별 가용 병상 집계")
def summary() -> list[SidoSummary]:
    return [SidoSummary.model_validate(row) for row in hospital_service.summary_by_sido()]


@router.get("/stream", summary="병상 실시간 갱신 스트림 (SSE)")
async def stream_bed_updates() -> StreamingResponse:
    async def events():
        since = datetime.now(timezone.utc)
        while True:
            await asyncio.sleep(_STREAM_POLL_S)
            rows = await asyncio.to_thread(hospital_service.list_updated_since, since)
            if rows:
                since = max(row["updated_at"] for row in rows)
                payload = [BedStatus.model_validate(row).model_dump(mode="json") for row in rows]
                yield f"data: {json.dumps(payload)}\n\n"
            else:
                yield ": keep-alive\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("/{hpid}", response_model=BedStatus, summary="기관 단건 조회")
def get_hospital(hpid: str) -> BedStatus:
    row = hospital_service.get_bed_status(hpid)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"hpid={hpid} 의 병상 정보를 찾을 수 없습니다.",
        )
    return BedStatus.model_validate(row)


@router.get(
    "/{hpid}/history",
    response_model=list[BedHistoryPoint],
    summary="기관 병상 이력 조회",
)
def get_hospital_history(
    hpid: str,
    limit: int = Query(default=48, ge=1, le=500, description="최근 N건"),
) -> list[BedHistoryPoint]:
    rows = hospital_service.list_bed_history(hpid, limit=limit)
    return [BedHistoryPoint.model_validate(row) for row in rows]
