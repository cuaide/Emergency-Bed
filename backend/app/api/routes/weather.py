from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query, status
from app.schemas import WeatherCoverage, WeatherCurrent, WeatherForecastPoint, WeatherResponse
from app.services import weather as weather_service

router = APIRouter(prefix="/weather", tags=["weather"])


@router.get("", response_model=WeatherResponse, summary="좌표 기준 실황 + 예보")
def get_weather(
    lat: float = Query(description="위도", ge=-90, le=90),
    lon: float = Query(description="경도", ge=-180, le=180),
    hours: int = Query(default=12, ge=1, le=72, description="예보 조회 시간 범위"),
) -> WeatherResponse:
    grid = weather_service.resolve_grid(lat, lon)
    current = weather_service.get_current(grid.nx, grid.ny)
    forecast = weather_service.list_forecast(grid.nx, grid.ny, hours=hours)
    return WeatherResponse(
        nx=grid.nx,
        ny=grid.ny,
        current=WeatherCurrent.model_validate(current) if current else None,
        forecast=[WeatherForecastPoint.model_validate(row) for row in forecast],
    )


@router.get("/coverage", response_model=WeatherCoverage, summary="날씨 적재 현황")
def coverage() -> WeatherCoverage:
    return WeatherCoverage.model_validate(weather_service.coverage())


@router.get(
    "/hospitals/{hpid}",
    response_model=WeatherCurrent,
    summary="병원 위치의 최신 실황",
)
def get_weather_for_hospital(hpid: str) -> WeatherCurrent:
    row = weather_service.get_current_for_hospital(hpid)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"hpid={hpid} 위치의 날씨 정보가 없습니다. (병원 좌표 또는 날씨 적재 확인)",
        )
    return WeatherCurrent.model_validate(row)
