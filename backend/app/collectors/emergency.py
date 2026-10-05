from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any
from xml.etree import ElementTree

import requests

from app import db
from app.config import Settings, get_settings
from app.grid import latlon_to_grid

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

PATH_REALTIME_BEDS = "getEmrrmRltmUsefulSckbdInfoInqire"  
PATH_HOSPITAL_LIST = "getEgytListInfoInqire"  

HV_NUMBERED_FIELDS: tuple[str, ...] = tuple(f"hv{index}" for index in range(1, 13))

INT_FIELDS: tuple[str, ...] = (
    "hvec",
    "hvoc",
    "hvcc",
    "hvncc",
    "hvccc",
    "hvicc",
    "hvgc",
    "hvs01",
    *HV_NUMBERED_FIELDS,
)
BOOL_FIELDS: tuple[str, ...] = (
    "hvctayn",
    "hvmriayn",
    "hvangioayn",
    "hvventiayn",
    "hvamyn",
)


class EmergencyApiError(RuntimeError):
    """공공 API가 정상 응답(resultCode=00)을 주지 않은 경우."""

def _text(element: ElementTree.Element) -> str:
    return (element.text or "").strip()


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in {"", "-", "null", "None"}:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().upper()
    if text in {"Y", "TRUE", "1"}:
        return True
    if text in {"N", "FALSE", "0"}:
        return False
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_hvidate(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=KST)

    text = str(value).strip()
    if not text:
        return None

    if "-" in text or "T" in text:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=KST)
        except ValueError:
            return None

    digits = "".join(ch for ch in text if ch.isdigit())
    for fmt, length in (("%Y%m%d%H%M%S", 14), ("%Y%m%d%H%M", 12), ("%Y%m%d", 8)):
        if len(digits) >= length:
            try:
                return datetime.strptime(digits[:length], fmt).replace(tzinfo=KST)
            except ValueError:
                continue
    return None


def parse_bed_item(raw: dict[str, str], sido: str | None = None) -> dict[str, Any] | None:
    """API item 하나를 DB 컬럼 형태로 변환. hpid/hvidate가 없으면 None."""
    hpid = (raw.get("hpid") or "").strip()
    hvidate = parse_hvidate(raw.get("hvidate"))
    if not hpid or hvidate is None:
        return None

    row: dict[str, Any] = {
        "hpid": hpid,
        "hvidate": hvidate,
        "duty_name": (raw.get("dutyName") or "").strip() or None,
        "sido": sido,
        "hvdnm": (raw.get("hvdnm") or "").strip() or None,
        "raw": {key: value for key, value in raw.items() if value not in (None, "")},
    }
    for field in INT_FIELDS:
        row[field] = _to_int(raw.get(field))
    for field in BOOL_FIELDS:
        row[field] = _to_bool(raw.get(field))
    return row


def parse_hospital_item(raw: dict[str, str]) -> dict[str, Any] | None:
    hpid = (raw.get("hpid") or "").strip()
    if not hpid:
        return None

    address = (raw.get("dutyAddr") or "").strip()
    parts = address.split()
    latitude = _to_float(raw.get("wgs84Lat"))
    longitude = _to_float(raw.get("wgs84Lon"))

    grid = latlon_to_grid(latitude, longitude) if latitude is not None and longitude is not None else None

    return {
        "hpid": hpid,
        "duty_name": (raw.get("dutyName") or "").strip() or hpid,
        "duty_div_name": (raw.get("dutyDivName") or "").strip() or None,
        "duty_addr": address or None,
        "duty_tel1": (raw.get("dutyTel1") or "").strip() or None,
        "duty_tel3": (raw.get("dutyTel3") or "").strip() or None,
        "duty_emcls": (raw.get("dutyEmcls") or "").strip() or None,
        "duty_emcls_name": (raw.get("dutyEmclsName") or "").strip() or None,
        "sido": parts[0] if parts else None,
        "sigungu": parts[1] if len(parts) > 1 else None,
        "post_cdn": ((raw.get("postCdn1") or "") + (raw.get("postCdn2") or "")).strip() or None,
        "latitude": latitude,
        "longitude": longitude,
        "nx": grid.nx if grid else None,
        "ny": grid.ny if grid else None,
        "raw": {key: value for key, value in raw.items() if value not in (None, "")},
    }

def _request_page(
    path: str,
    params: dict[str, Any],
    settings: Settings,
    session: requests.Session,
) -> tuple[list[dict[str, str]], int]:
    url = f"{settings.api_base_url}/{path}"
    last_error: Exception | None = None

    for attempt in range(settings.api_retry + 1):
        try:
            response = session.get(url, params=params, timeout=settings.api_timeout)
            response.raise_for_status()
            root = ElementTree.fromstring(response.content)
            break
        except (requests.RequestException, ElementTree.ParseError) as exc:
            last_error = exc
            if attempt >= settings.api_retry:
                raise EmergencyApiError(f"{path} 호출 실패: {exc}") from exc
            sleep_for = 2**attempt
            logger.warning("%s 호출 재시도 %d회차 (%s)", path, attempt + 1, exc)
            time.sleep(sleep_for)
    else:  
        raise EmergencyApiError(f"{path} 호출 실패: {last_error}")

    result_code = root.findtext(".//resultCode", default="").strip()
    if result_code and result_code != "00":
        message = root.findtext(".//resultMsg", default="").strip()
        raise EmergencyApiError(f"{path} resultCode={result_code} resultMsg={message}")

    items: list[dict[str, str]] = []
    for item in root.iter("item"):
        items.append({child.tag: _text(child) for child in item})

    total_count = _to_int(root.findtext(".//totalCount")) or 0
    return items, total_count


def fetch_bed_status(districts: list[str] | None = None) -> list[dict]:
    settings = get_settings()
    if not settings.service_key:
        raise EmergencyApiError("DATA_GO_KR_SERVICE_KEY 환경 변수가 비어 있습니다.")

    targets = tuple(districts) if districts else settings.districts
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, datetime]] = set()

    with requests.Session() as session:
        for sido in targets:
            page = 1
            while page <= settings.api_max_pages:
                params = {
                    "serviceKey": settings.service_key,
                    "STAGE1": sido,
                    "pageNo": page,
                    "numOfRows": settings.api_num_of_rows,
                }
                items, total_count = _request_page(PATH_REALTIME_BEDS, params, settings, session)
                if not items:
                    break

                for raw in items:
                    row = parse_bed_item(raw, sido=sido)
                    if row is None:
                        continue
                    key = (row["hpid"], row["hvidate"])
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(row)

                if page * settings.api_num_of_rows >= total_count:
                    break
                page += 1

            logger.debug("%s 수집 누적 %d건", sido, len(rows))

    logger.info("병상 수집 완료: %d건 (지역 %d곳)", len(rows), len(targets))
    return rows


def fetch_hospitals(districts: list[str] | None = None) -> list[dict]:
    settings = get_settings()
    if not settings.service_key:
        raise EmergencyApiError("DATA_GO_KR_SERVICE_KEY 환경 변수가 비어 있습니다.")

    targets = tuple(districts) if districts else settings.districts
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    with requests.Session() as session:
        for sido in targets:
            page = 1
            while page <= settings.api_max_pages:
                params = {
                    "serviceKey": settings.service_key,
                    "Q0": sido,
                    "pageNo": page,
                    "numOfRows": settings.api_num_of_rows,
                }
                items, total_count = _request_page(PATH_HOSPITAL_LIST, params, settings, session)
                if not items:
                    break

                for raw in items:
                    row = parse_hospital_item(raw)
                    if row is None or row["hpid"] in seen:
                        continue
                    seen.add(row["hpid"])
                    rows.append(row)

                if page * settings.api_num_of_rows >= total_count:
                    break
                page += 1

    logger.info("기관 기본정보 수집 완료: %d건", len(rows))
    return rows


def normalize_bed_row(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None


    hpid = (row.get("hpid") or "").strip() if isinstance(row.get("hpid"), str) else row.get("hpid")
    hvidate = parse_hvidate(row.get("hvidate"))
    if not hpid or hvidate is None:
        return None

    normalized: dict[str, Any] = {
        "hpid": hpid,
        "hvidate": hvidate,
        "duty_name": row.get("duty_name") or row.get("dutyName"),
        "sido": row.get("sido"),
        "hvdnm": row.get("hvdnm"),
        "raw": row.get("raw") if isinstance(row.get("raw"), dict) else {},
    }
    for field in INT_FIELDS:
        normalized[field] = _to_int(row.get(field))
    for field in BOOL_FIELDS:
        normalized[field] = _to_bool(row.get(field))
    return normalized


def validate_bed_rows(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    valid: list[dict[str, Any]] = []
    dropped = 0
    for row in rows:
        normalized = normalize_bed_row(row)
        if normalized is None:
            dropped += 1
            continue
        valid.append(normalized)
    return valid, dropped


def save_bed_status(rows: list[dict]) -> int:
    valid, dropped = validate_bed_rows(rows)
    if dropped:
        logger.warning("유효하지 않은 병상 메시지 %d건 폐기", dropped)
    if not valid:
        return 0
    return db.upsert_bed_status(valid)


def save_hospitals(rows: list[dict]) -> int:
    valid = [row for row in rows if isinstance(row, dict) and row.get("hpid")]
    if not valid:
        return 0
    return db.upsert_hospitals(valid)


def collect_and_save_bed_status(districts: list[str] | None = None) -> int:
    return save_bed_status(fetch_bed_status(districts))
