"""기상청 단기예보 조회서비스 수집기.

emergency.py와 같은 규칙으로 "API 호출/파싱"과 "DB 적재"를 분리한다.

    fetch_weather_current(grids)  → save_weather_current(rows)
    fetch_weather_forecast(grids) → save_weather_forecast(rows)

수집 대상 격자는 hospitals 테이블에 적재된 병원 좌표에서 유도한다.
병원이 몰려 있는 격자는 중복 제거되므로 호출 수가 병원 수보다 훨씬 적다.
"""

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

PATH_CURRENT = "getUltraSrtNcst"  # 초단기실황
PATH_FORECAST = "getVilageFcst"  # 단기예보

# 단기예보 발표 시각 (매일 8회)
FORECAST_BASE_HOURS = (2, 5, 8, 11, 14, 17, 20, 23)

# 실황: 매시 정각 관측, 매시 40분 이후 제공
CURRENT_RELEASE_MINUTE = 40
# 예보: 발표 시각 10분 이후 제공
FORECAST_RELEASE_MINUTE = 10

# 실황 카테고리 → 컬럼
CURRENT_CATEGORIES = {
    "T1H": "t1h",  # 기온(°C)
    "RN1": "rn1",  # 1시간 강수량(mm)
    "REH": "reh",  # 습도(%)
    "WSD": "wsd",  # 풍속(m/s)
    "PTY": "pty",  # 강수형태(코드)
    "VEC": "vec",  # 풍향(deg)
}

# 예보 카테고리 → 컬럼
FORECAST_CATEGORIES = {
    "TMP": "tmp",  # 1시간 기온(°C)
    "POP": "pop",  # 강수확률(%)
    "PTY": "pty",  # 강수형태(코드)
    "SKY": "sky",  # 하늘상태(코드)
    "REH": "reh",  # 습도(%)
    "WSD": "wsd",  # 풍속(m/s)
    "PCP": "pcp",  # 1시간 강수량(문자열: "강수없음", "1.0mm" 등)
}

INT_COLUMNS = {"pty", "sky", "pop"}
TEXT_COLUMNS = {"pcp"}


class WeatherApiError(RuntimeError):
    """기상청 API가 정상 응답(resultCode=00)을 주지 않은 경우."""


# ---------------------------------------------------------------------------
# 발표 시각 계산
# ---------------------------------------------------------------------------


def current_base_datetime(now: datetime | None = None) -> datetime:
    """지금 시점에 조회 가능한 초단기실황 발표 시각(정시)을 구한다."""
    now = (now or datetime.now(KST)).astimezone(KST)
    base = now.replace(minute=0, second=0, microsecond=0)
    if now.minute < CURRENT_RELEASE_MINUTE:
        base -= timedelta(hours=1)
    return base


def forecast_base_datetime(now: datetime | None = None) -> datetime:
    """지금 시점에 조회 가능한 단기예보 발표 시각을 구한다."""
    now = (now or datetime.now(KST)).astimezone(KST)
    candidate = now.replace(minute=0, second=0, microsecond=0)

    for _ in range(24):
        if candidate.hour in FORECAST_BASE_HOURS:
            released = candidate + timedelta(minutes=FORECAST_RELEASE_MINUTE)
            if released <= now:
                return candidate
        candidate -= timedelta(hours=1)
    # 도달할 수 없음 (24시간 안에 반드시 발표 시각이 있다)
    raise WeatherApiError("단기예보 발표 시각을 계산하지 못했습니다.")


def _parse_fcst_datetime(date_text: str, time_text: str) -> datetime | None:
    try:
        return datetime.strptime(f"{date_text}{time_text.zfill(4)}", "%Y%m%d%H%M").replace(tzinfo=KST)
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# 값 변환
# ---------------------------------------------------------------------------


def _to_number(column: str, value: Any) -> Any:
    """카테고리별로 int / float / str 중 맞는 타입으로 변환한다."""
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
    # 기상청은 결측을 -99, -998, -999 등으로 표기한다.
    # 국내 실측값이 -90 아래로 내려가는 항목은 없으므로 이 선에서 자른다.
    if number <= -90:
        return None
    if column in INT_COLUMNS:
        return int(number)
    return number


# ---------------------------------------------------------------------------
# API 호출
# ---------------------------------------------------------------------------


def _request_items(
    path: str,
    params: dict[str, Any],
    settings: Settings,
    session: requests.Session,
) -> list[dict[str, Any]]:
    """기상청 API 한 번 호출해 items 배열을 돌려준다 (JSON)."""
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
        # 03 = NO_DATA. 특정 격자에 자료가 없는 건 오류가 아니라 빈 결과로 취급한다.
        if result_code == "03":
            return []
        raise WeatherApiError(
            f"{path} resultCode={result_code} resultMsg={header.get('resultMsg')}"
        )

    body = (payload.get("response") or {}).get("body") or {}
    items = (body.get("items") or {}).get("item") or []
    return items if isinstance(items, list) else [items]


def target_grids(limit: int | None = None) -> list[Grid]:
    """hospitals 테이블 좌표에서 중복 없는 격자 목록을 만든다."""
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
    """수집 대상 격자를 하나도 찾지 못한 경우.

    조용히 0건으로 끝내면 원인을 찾기 어려워서 명시적으로 실패시킨다.
    """


def _resolve_grids(grids: list[Grid] | list[tuple[int, int]] | None) -> list[Grid]:
    settings = get_settings()
    if grids:
        return [grid if isinstance(grid, Grid) else Grid(*grid) for grid in grids]

    resolved = target_grids(limit=settings.weather_max_grids)
    if resolved:
        return resolved

    # 좌표는 있는데 격자만 비어 있는 경우(예전 방식으로 적재된 데이터)는 스스로 복구한다.
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
    """초단기실황을 조회해 격자별 1행으로 반환한다 (DB 접근은 격자 조회뿐)."""
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
    """단기예보를 조회해 (격자, 예보시각)별 1행으로 반환한다."""
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
# 저장
# ---------------------------------------------------------------------------


def _valid_current(row: Any) -> bool:
    return (
        isinstance(row, dict)
        and row.get("nx") is not None
        and row.get("ny") is not None
        and row.get("base_datetime") is not None
    )


def save_weather_current(rows: list[dict]) -> int:
    """실황을 weather_current에 UPSERT한다."""
    valid = [row for row in rows if _valid_current(row)]
    dropped = len(rows) - len(valid)
    if dropped:
        logger.warning("유효하지 않은 실황 %d건 폐기", dropped)
    if not valid:
        return 0
    return db.upsert_weather_current(valid)


def save_weather_forecast(rows: list[dict]) -> int:
    """예보를 weather_forecast에 UPSERT한다."""
    valid = [row for row in rows if _valid_current(row) and row.get("fcst_datetime") is not None]
    dropped = len(rows) - len(valid)
    if dropped:
        logger.warning("유효하지 않은 예보 %d건 폐기", dropped)
    if not valid:
        return 0
    return db.upsert_weather_forecast(valid)


def backfill_hospital_grids() -> int:
    """좌표는 있는데 nx/ny가 비어 있는 병원의 격자를 채운다."""
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
