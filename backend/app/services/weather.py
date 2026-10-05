"""weather_current / weather_forecast 조회 서비스."""

from __future__ import annotations

from typing import Any

from app import db
from app.grid import Grid, latlon_to_grid

# 기상청 코드 → 한글
PTY_LABELS = {
    0: "없음",
    1: "비",
    2: "비/눈",
    3: "눈",
    4: "소나기",
    5: "빗방울",
    6: "빗방울눈날림",
    7: "눈날림",
}
SKY_LABELS = {1: "맑음", 3: "구름많음", 4: "흐림"}


def _decorate(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    decorated = dict(row)
    if "pty" in decorated:
        decorated["pty_label"] = PTY_LABELS.get(decorated.get("pty"))
    if "sky" in decorated:
        decorated["sky_label"] = SKY_LABELS.get(decorated.get("sky"))
    return decorated


def resolve_grid(latitude: float, longitude: float) -> Grid:
    return latlon_to_grid(latitude, longitude)


def get_current(nx: int, ny: int) -> dict[str, Any] | None:
    """격자의 최신 실황 1건."""
    row = db.fetch_one(
        """
        SELECT nx, ny, base_datetime, t1h, rn1, reh, wsd, vec, pty, collected_at 
        FROM weather_current
        WHERE nx = %(nx)s AND ny = %(ny)s
        ORDER BY base_datetime DESC
        LIMIT 1
        """,
        {"nx": nx, "ny": ny},
    )
    return _decorate(row)


def get_current_by_latlon(latitude: float, longitude: float) -> dict[str, Any] | None:
    grid = resolve_grid(latitude, longitude)
    return get_current(grid.nx, grid.ny)


def list_forecast(nx: int, ny: int, *, hours: int = 24) -> list[dict[str, Any]]:
    """격자의 향후 예보. 예보 시각별로 가장 최근 발표분만 남긴다."""
    rows = db.fetch_all(
        """
        SELECT DISTINCT ON (fcst_datetime)
            nx, ny, base_datetime, fcst_datetime,
            tmp, reh, wsd, pop, pty, sky, pcp, collected_at
        FROM weather_forecast
        WHERE nx = %(nx)s AND ny = %(ny)s
          AND fcst_datetime >= now()
          AND fcst_datetime <= now() + make_interval(hours => %(hours)s)
        ORDER BY fcst_datetime ASC, base_datetime DESC
        """,
        {"nx": nx, "ny": ny, "hours": hours},
    )
    return [_decorate(row) for row in rows]


def list_forecast_by_latlon(
    latitude: float, longitude: float, *, hours: int = 24
) -> list[dict[str, Any]]:
    grid = resolve_grid(latitude, longitude)
    return list_forecast(grid.nx, grid.ny, hours=hours)


def get_current_for_hospital(hpid: str) -> dict[str, Any] | None:
    """병원이 속한 격자의 최신 실황."""
    row = db.fetch_one(
        """
        SELECT w.nx, w.ny, w.base_datetime, w.t1h, w.rn1, w.reh, w.wsd, w.vec, w.pty,
               w.collected_at
        FROM hospitals AS h
        JOIN weather_current AS w ON w.nx = h.nx AND w.ny = h.ny
        WHERE h.hpid = %(hpid)s
        ORDER BY w.base_datetime DESC
        LIMIT 1
        """,
        {"hpid": hpid},
    )
    return _decorate(row)


def coverage() -> dict[str, Any]:
    """날씨 적재 현황 (파이프라인 점검용)."""
    return db.fetch_one(
        """
        SELECT
            (SELECT count(DISTINCT (nx, ny)) FROM weather_current)  AS current_grids,
            (SELECT count(*) FROM weather_current)                  AS current_rows,
            (SELECT max(base_datetime) FROM weather_current)        AS current_base,
            (SELECT count(DISTINCT (nx, ny)) FROM weather_forecast) AS forecast_grids,
            (SELECT count(*) FROM weather_forecast)                 AS forecast_rows,
            (SELECT max(base_datetime) FROM weather_forecast)       AS forecast_base
        """
    ) or {}
