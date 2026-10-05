from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

import requests

from app import db
from app.collectors.emergency import KST
from app.config import Settings, get_settings
from app.grid import Grid, latlon_to_grid

logger = logging.getLogger(__name__)

PATH_CURRENT = "getUltraSrtNcst" 
PATH_FORECAST = "getVilageFcst"  
FORECAST_BASE_HOURS = (2, 5, 8, 11, 14, 17, 20, 23)
CURRENT_RELEASE_MINUTE = 40
FORECAST_RELEASE_MINUTE = 10

CURRENT_CATEGORIES = {
    "T1H": "t1h",  # Temperature (°C)
    "RN1": "rn1",  # 1-hour precipitation (mm)
    "REH": "reh",  # Humidity (%)
    "WSD": "wsd",  # Wind speed (m/s)
    "PTY": "pty",  # Precipitation type (code)
    "VEC": "vec",  # Wind direction (deg)
}

# Forecast category → column
FORECAST_CATEGORIES = {
    "TMP": "tmp",  # 1-hour temperature (°C)
    "POP": "pop",  # Probability of precipitation (%)
    "PTY": "pty",  # Precipitation type (code)
    "SKY": "sky",  # Sky condition (code)
    "REH": "reh",  # Humidity (%)
    "WSD": "wsd",  # Wind speed (m/s)
    "PCP": "pcp",  # 1-hour precipitation (string: "강수없음", "1.0mm", etc.)
}

INT_COLUMNS = {"pty", "sky", "pop"}
TEXT_COLUMNS = {"pcp"}


class WeatherApiError(RuntimeError):
    """Raised when the KMA API does not return a normal response (resultCode=00)."""


# ---------------------------------------------------------------------------
# Release time calculation
# ---------------------------------------------------------------------------


def current_base_datetime(now: datetime | None = None) -> datetime:
    """Find the ultra-short-term observation release time (on the hour) available as of now."""
    now = (now or datetime.now(KST)).astimezone(KST)
    base = now.replace(minute=0, second=0, microsecond=0)
    if now.minute < CURRENT_RELEASE_MINUTE:
        base -= timedelta(hours=1)
    return base


def forecast_base_datetime(now: datetime | None = None) -> datetime:
    """Find the short-term forecast release time available as of now."""
    now = (now or datetime.now(KST)).astimezone(KST)
    candidate = now.replace(minute=0, second=0, microsecond=0)

    for _ in range(24):
        if candidate.hour in FORECAST_BASE_HOURS:
            released = candidate + timedelta(minutes=FORECAST_RELEASE_MINUTE)
            if released <= now:
                return candidate
        candidate -= timedelta(hours=1)
    # Unreachable (there is always a release time within 24 hours)
    raise WeatherApiError("단기예보 발표 시각을 계산하지 못했습니다.")


def _parse_fcst_datetime(date_text: str, time_text: str) -> datetime | None:
    try:
        return datetime.strptime(f"{date_text}{time_text.zfill(4)}", "%Y%m%d%H%M").replace(tzinfo=KST)
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Value conversion
# ---------------------------------------------------------------------------


def _to_number(column: str, value: Any) -> Any:
    """Convert to the right type per category: int / float / str."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if column in TEXT_COLUMNS:
        return text
    try:
        number = float(text)
    except ValueError:
        return None
    # KMA marks missing values as -99, -998, -999, etc.
    # No item measured in Korea drops below -90, so cut off at that line.
    if number <= -90:
        return None
    if column in INT_COLUMNS:
        return int(number)
    return number


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------


def _request_items(
    path: str,
    params: dict[str, Any],
    settings: Settings,
    session: requests.Session,
) -> list[dict[str, Any]]:
    """Call the KMA API once and return the items array (JSON)."""
    url = f"{settings.weather_base_url}/{path}"

    for attempt in range(settings.api_retry + 1):
        try:
            response = session.get(url, params=params, timeout=settings.api_timeout)
            response.raise_for_status()
            payload = response.json()
            break
        except (requests.RequestException, ValueError) as exc:
            if attempt >= settings.api_retry:
                raise WeatherApiError(f"{path} 호출 실패: {exc}") from exc
            logger.warning("%s 호출 재시도 %d회차 (%s)", path, attempt + 1, exc)
            time.sleep(2**attempt)

    header = (payload.get("response") or {}).get("header") or {}
    result_code = str(header.get("resultCode") or "").strip()
    if result_code and result_code != "00":
        # 03 = NO_DATA. No data for a particular grid is treated as an empty result, not an error.
        if result_code == "03":
            return []
        raise WeatherApiError(
            f"{path} resultCode={result_code} resultMsg={header.get('resultMsg')}"
        )

    body = (payload.get("response") or {}).get("body") or {}
    items = (body.get("items") or {}).get("item") or []
    return items if isinstance(items, list) else [items]


def target_grids(limit: int | None = None) -> list[Grid]:
    """Build a de-duplicated list of grids from the coordinates in the hospitals table."""
    rows = db.fetch_all(
        """
        SELECT DISTINCT nx, ny
        FROM hospitals
        WHERE nx IS NOT NULL AND ny IS NOT NULL
        ORDER BY nx, ny
        """
    )
    grids = [Grid(row["nx"], row["ny"]) for row in rows]
    if limit is not None:
        grids = grids[:limit]
    return grids


class NoTargetGridError(WeatherApiError):
    """Raised when no grid to collect could be found.

    Silently finishing with 0 rows makes the cause hard to track down, so it fails explicitly.
    """


def _resolve_grids(grids: list[Grid] | list[tuple[int, int]] | None) -> list[Grid]:
    settings = get_settings()
    if grids:
        return [grid if isinstance(grid, Grid) else Grid(*grid) for grid in grids]

    resolved = target_grids(limit=settings.weather_max_grids)
    if resolved:
        return resolved

    # If coordinates exist but grids are empty (data loaded the old way), recover automatically.
    if backfill_hospital_grids():
        resolved = target_grids(limit=settings.weather_max_grids)
        if resolved:
            return resolved

    raise NoTargetGridError(
        "수집 대상 격자가 없습니다. "
        "병원 기본정보를 먼저 적재하거나(python -m scripts.collect_beds --hospitals) "
        "격자를 직접 지정하세요(--grid 60,127)."
    )


def fetch_weather_current(
    grids: list[Grid] | list[tuple[int, int]] | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Fetch ultra-short-term observations and return one row per grid (the only DB access is the grid lookup)."""
    settings = get_settings()
    if not settings.weather_service_key:
        raise WeatherApiError("WEATHER_API_KEY / DATA_GO_KR_SERVICE_KEY 환경 변수가 비어 있습니다.")

    targets = _resolve_grids(grids)
    base = current_base_datetime(now)
    rows: list[dict[str, Any]] = []

    with requests.Session() as session:
        for grid in targets:
            params = {
                "serviceKey": settings.weather_service_key,
                "dataType": "JSON",
                "numOfRows": 100,
                "pageNo": 1,
                "base_date": base.strftime("%Y%m%d"),
                "base_time": base.strftime("%H%M"),
                "nx": grid.nx,
                "ny": grid.ny,
            }
            items = _request_items(PATH_CURRENT, params, settings, session)
            if not items:
                continue

            row: dict[str, Any] = {
                "nx": grid.nx,
                "ny": grid.ny,
                "base_datetime": base,
                "raw": {},
            }
            for item in items:
                category = str(item.get("category") or "").strip()
                value = item.get("obsrValue")
                row["raw"][category] = value
                column = CURRENT_CATEGORIES.get(category)
                if column:
                    row[column] = _to_number(column, value)
            rows.append(row)

    logger.info("날씨 실황 수집 완료: 격자 %d곳 / %d건 (기준 %s)", len(targets), len(rows), base)
    return rows


def fetch_weather_forecast(
    grids: list[Grid] | list[tuple[int, int]] | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Fetch the short-term forecast and return one row per (grid, forecast time)."""
    settings = get_settings()
    if not settings.weather_service_key:
        raise WeatherApiError("WEATHER_API_KEY / DATA_GO_KR_SERVICE_KEY 환경 변수가 비어 있습니다.")

    targets = _resolve_grids(grids)
    base = forecast_base_datetime(now)
    horizon = base + timedelta(hours=settings.weather_forecast_hours)
    rows: list[dict[str, Any]] = []

    with requests.Session() as session:
        for grid in targets:
            params = {
                "serviceKey": settings.weather_service_key,
                "dataType": "JSON",
                "numOfRows": 1000,
                "pageNo": 1,
                "base_date": base.strftime("%Y%m%d"),
                "base_time": base.strftime("%H%M"),
                "nx": grid.nx,
                "ny": grid.ny,
            }
            items = _request_items(PATH_FORECAST, params, settings, session)

            by_fcst: dict[datetime, dict[str, Any]] = {}
            for item in items:
                fcst_at = _parse_fcst_datetime(
                    str(item.get("fcstDate") or ""), str(item.get("fcstTime") or "")
                )
                if fcst_at is None or fcst_at > horizon:
                    continue

                row = by_fcst.setdefault(
                    fcst_at,
                    {
                        "nx": grid.nx,
                        "ny": grid.ny,
                        "base_datetime": base,
                        "fcst_datetime": fcst_at,
                        "raw": {},
                    },
                )
                category = str(item.get("category") or "").strip()
                value = item.get("fcstValue")
                row["raw"][category] = value
                column = FORECAST_CATEGORIES.get(category)
                if column:
                    row[column] = _to_number(column, value)

            rows.extend(by_fcst[key] for key in sorted(by_fcst))

    logger.info("날씨 예보 수집 완료: 격자 %d곳 / %d건 (기준 %s)", len(targets), len(rows), base)
    return rows


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _valid_current(row: Any) -> bool:
    return (
        isinstance(row, dict)
        and row.get("nx") is not None
        and row.get("ny") is not None
        and row.get("base_datetime") is not None
    )


def save_weather_current(rows: list[dict]) -> int:
    """UPSERT observations into weather_current."""
    valid = [row for row in rows if _valid_current(row)]
    dropped = len(rows) - len(valid)
    if dropped:
        logger.warning("유효하지 않은 실황 %d건 폐기", dropped)
    if not valid:
        return 0
    return db.upsert_weather_current(valid)


def save_weather_forecast(rows: list[dict]) -> int:
    """UPSERT forecasts into weather_forecast."""
    valid = [row for row in rows if _valid_current(row) and row.get("fcst_datetime") is not None]
    dropped = len(rows) - len(valid)
    if dropped:
        logger.warning("유효하지 않은 예보 %d건 폐기", dropped)
    if not valid:
        return 0
    return db.upsert_weather_forecast(valid)


def backfill_hospital_grids() -> int:
    """Fill in grids for hospitals that have coordinates but empty nx/ny."""
    rows = db.fetch_all(
        """
        SELECT hpid, latitude, longitude
        FROM hospitals
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
          AND (nx IS NULL OR ny IS NULL)
        """
    )
    if not rows:
        return 0

    params = []
    for row in rows:
        grid = latlon_to_grid(row["latitude"], row["longitude"])
        params.append((grid.nx, grid.ny, row["hpid"]))

    with db.connection() as conn, conn.cursor() as cur:
        cur.executemany("UPDATE hospitals SET nx = %s, ny = %s WHERE hpid = %s", params)
    logger.info("병원 격자 좌표 보정 %d건", len(params))
    return len(params)