"""bed_status_latest 조회 서비스. FastAPI 라우터가 이 모듈만 사용한다."""

from __future__ import annotations

from typing import Any

from app import db
from app.config import get_settings

# hospitals(기본정보) LEFT JOIN bed_status_latest(최신 병상)
_SELECT_COLUMNS = """
    b.hpid,
    COALESCE(h.duty_name, b.duty_name)            AS duty_name,
    COALESCE(h.sido, b.sido)                      AS sido,
    h.sigungu,
    h.duty_addr,
    h.duty_tel3,
    h.duty_emcls_name,
    h.latitude,
    h.longitude,
    b.hvidate,
    b.hvec,
    b.hvoc,
    b.hvcc,
    b.hvncc,
    b.hvccc,
    b.hvicc,
    b.hvgc,
    b.hvs01,
    b.hv1,
    b.hv2,
    b.hv3,
    b.hv4,
    b.hv5,
    b.hv6,
    b.hv7,
    b.hv8,
    b.hv9,
    b.hv10,
    b.hv11,
    b.hv12,
    b.hvctayn,
    b.hvmriayn,
    b.hvangioayn,
    b.hvventiayn,
    b.hvamyn,
    b.hvdnm,
    b.collected_at,
    b.updated_at,
    (b.hvidate < now() - make_interval(mins => %(stale_minutes)s)) AS is_stale
"""

_FROM_JOIN = """
FROM bed_status_latest AS b
LEFT JOIN hospitals AS h ON h.hpid = b.hpid
"""

_BASE_SELECT = f"SELECT{_SELECT_COLUMNS}{_FROM_JOIN}"

# 자원 컬럼 화이트리스트. find_with_resources 가 컬럼명을 SQL 문자열에 직접 끼워 넣으므로
# 외부 입력(프로파일 정의)이 그대로 들어가지 않도록 여기 있는 이름만 허용한다.
BED_RESOURCE_COLUMNS: frozenset[str] = frozenset(
    {"hvec", "hvoc", "hvcc", "hvncc", "hvccc", "hvicc", "hvgc", "hvs01"}
    | {f"hv{index}" for index in range(1, 13)}
)


def _stale_minutes() -> int:
    return get_settings().stale_after_minutes


def _build_filters(
    *,
    sido: str | None,
    sigungu: str | None,
    query: str | None,
    only_available: bool,
    require_ct: bool,
    require_mri: bool,
    require_ventilator: bool,
    include_stale: bool,
) -> tuple[str, dict[str, Any]]:
    """목록/카운트가 동일한 조건을 쓰도록 WHERE 절과 파라미터를 함께 만든다."""
    params: dict[str, Any] = {"stale_minutes": _stale_minutes()}
    where: list[str] = []

    if sido:
        where.append("COALESCE(h.sido, b.sido) = %(sido)s")
        params["sido"] = sido
    if sigungu:
        where.append("h.sigungu = %(sigungu)s")
        params["sigungu"] = sigungu
    if query:
        where.append(
            "(COALESCE(h.duty_name, b.duty_name) ILIKE %(query)s OR h.duty_addr ILIKE %(query)s)"
        )
        params["query"] = f"%{query}%"
    if only_available:
        where.append("b.hvec > 0")
    if require_ct:
        where.append("b.hvctayn IS TRUE")
    if require_mri:
        where.append("b.hvmriayn IS TRUE")
    if require_ventilator:
        where.append("b.hvventiayn IS TRUE")
    if not include_stale:
        where.append("b.hvidate >= now() - make_interval(mins => %(stale_minutes)s)")

    clause = ("WHERE " + " AND ".join(where) + "\n") if where else ""
    return clause, params


def list_bed_status(
    *,
    sido: str | None = None,
    sigungu: str | None = None,
    query: str | None = None,
    only_available: bool = False,
    require_ct: bool = False,
    require_mri: bool = False,
    require_ventilator: bool = False,
    include_stale: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """조건에 맞는 최신 병상 목록을 응급실 가용 병상 내림차순으로 반환."""
    clause, params = _build_filters(
        sido=sido,
        sigungu=sigungu,
        query=query,
        only_available=only_available,
        require_ct=require_ct,
        require_mri=require_mri,
        require_ventilator=require_ventilator,
        include_stale=include_stale,
    )
    params["limit"] = limit
    params["offset"] = offset

    sql = (
        _BASE_SELECT
        + clause
        + "ORDER BY b.hvec DESC NULLS LAST, b.hvidate DESC\nLIMIT %(limit)s OFFSET %(offset)s"
    )
    return db.fetch_all(sql, params)


def count_bed_status(
    *,
    sido: str | None = None,
    sigungu: str | None = None,
    query: str | None = None,
    only_available: bool = False,
    require_ct: bool = False,
    require_mri: bool = False,
    require_ventilator: bool = False,
    include_stale: bool = True,
) -> int:
    """list_bed_status와 동일한 조건의 전체 건수 (페이지네이션용)."""
    clause, params = _build_filters(
        sido=sido,
        sigungu=sigungu,
        query=query,
        only_available=only_available,
        require_ct=require_ct,
        require_mri=require_mri,
        require_ventilator=require_ventilator,
        include_stale=include_stale,
    )
    sql = f"SELECT count(*) AS total{_FROM_JOIN}{clause}"
    row = db.fetch_one(sql, params)
    return int(row["total"]) if row else 0


def get_bed_status(hpid: str) -> dict[str, Any] | None:
    sql = _BASE_SELECT + "WHERE b.hpid = %(hpid)s"
    return db.fetch_one(sql, {"hpid": hpid, "stale_minutes": _stale_minutes()})


def list_bed_history(hpid: str, *, limit: int = 48) -> list[dict[str, Any]]:
    """특정 기관의 병상 이력(최근 순)."""
    sql = """
    SELECT hpid, hvidate, hvec, hvoc, hvicc, hvgc, hvs01, collected_at
    FROM bed_status
    WHERE hpid = %(hpid)s
    ORDER BY hvidate DESC
    LIMIT %(limit)s
    """
    return db.fetch_all(sql, {"hpid": hpid, "limit": limit})


def find_nearby(
    *,
    latitude: float,
    longitude: float,
    radius_km: float = 10.0,
    only_available: bool = True,
    include_stale: bool = True,
    limit: int = 20,
    hpids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """좌표 기준 반경 내 응급실을 거리순으로 반환 (haversine).

    Tmap 경로 호출은 이 결과 상위 후보에만 적용하면 된다.
    hpids 를 주면 그 기관들로만 좁힌다 — 순위가 확정된 뒤 상위 후보에만 경로를 붙일 때 쓴다.
    """
    params: dict[str, Any] = {
        "lat": latitude,
        "lon": longitude,
        "radius_km": radius_km,
        # 위경도 사각형 선필터 (인덱스 활용)
        "lat_delta": radius_km / 111.0,
        "lon_delta": radius_km / 88.0,
        "only_available": only_available,
        "include_stale": include_stale,
        "stale_minutes": _stale_minutes(),
        "limit": limit,
    }
    distance_expr = """
    (6371 * acos(LEAST(1.0,
        cos(radians(%(lat)s)) * cos(radians(h.latitude)) *
        cos(radians(h.longitude) - radians(%(lon)s)) +
        sin(radians(%(lat)s)) * sin(radians(h.latitude))
    ))) AS distance_km"""

    inner = f"""SELECT{_SELECT_COLUMNS},{distance_expr}
{_FROM_JOIN}
WHERE h.latitude IS NOT NULL
  AND h.longitude IS NOT NULL
  AND h.latitude BETWEEN %(lat)s - %(lat_delta)s AND %(lat)s + %(lat_delta)s
  AND h.longitude BETWEEN %(lon)s - %(lon_delta)s AND %(lon)s + %(lon_delta)s
  AND (%(only_available)s IS FALSE OR b.hvec > 0)
  AND (%(include_stale)s IS TRUE OR b.hvidate >= now() - make_interval(mins => %(stale_minutes)s))"""

    if hpids:
        params["hpids"] = list(hpids)
        inner += "\n  AND b.hpid = ANY(%(hpids)s)"

    # distance_km는 WHERE에서 바로 못 쓰므로 서브쿼리로 감싸 거리 필터를 적용한다.
    sql = f"""
SELECT * FROM (
{inner}
) AS nearby
WHERE distance_km <= %(radius_km)s
ORDER BY distance_km ASC
LIMIT %(limit)s
"""
    return db.fetch_all(sql, params)


def list_updated_since(since: Any) -> list[dict[str, Any]]:
    """since 이후 bed_status_latest 가 갱신된 행. SSE 스트림이 폴링에 쓴다."""
    sql = _BASE_SELECT + "WHERE b.updated_at > %(since)s\nORDER BY b.updated_at ASC"
    return db.fetch_all(sql, {"since": since, "stale_minutes": _stale_minutes()})


def find_with_resources(
    *,
    latitude: float,
    longitude: float,
    radius_km: float,
    columns: list[str],
    limit: int = 8,
) -> list[dict[str, Any]]:
    """반경 안에서 지정한 자원 컬럼 중 하나라도 값이 있는 기관을 거리순으로 반환한다.

    거리순 상위 N개 컷에서 밀려난 전문병원(화상·외상 등)을 후보에 채워 넣을 때 쓴다.
    INTEGER 컬럼이 NULL 이어도 원본 raw JSONB 에 값이 남아 있는 항목(Y/N 로 오는 hv5·hv7 등)이
    있으므로 두 곳을 함께 본다.
    """
    allowed = {c for c in BED_RESOURCE_COLUMNS if c in set(columns)}
    if not allowed:
        return []

    checks = " OR ".join(
        f"b.{c} IS NOT NULL OR (b.raw ->> '{c}') IS NOT NULL" for c in sorted(allowed)
    )
    params: dict[str, Any] = {
        "lat": latitude,
        "lon": longitude,
        "radius_km": radius_km,
        "lat_delta": radius_km / 111.0,
        "lon_delta": radius_km / 88.0,
        "stale_minutes": _stale_minutes(),
        "limit": limit,
    }
    distance_expr = """
    (6371 * acos(LEAST(1.0,
        cos(radians(%(lat)s)) * cos(radians(h.latitude)) *
        cos(radians(h.longitude) - radians(%(lon)s)) +
        sin(radians(%(lat)s)) * sin(radians(h.latitude))
    ))) AS distance_km"""

    inner = f"""SELECT{_SELECT_COLUMNS},{distance_expr}
{_FROM_JOIN}
WHERE h.latitude IS NOT NULL
  AND h.longitude IS NOT NULL
  AND h.latitude BETWEEN %(lat)s - %(lat_delta)s AND %(lat)s + %(lat_delta)s
  AND h.longitude BETWEEN %(lon)s - %(lon_delta)s AND %(lon)s + %(lon_delta)s
  AND ({checks})"""

    sql = f"""
SELECT * FROM (
{inner}
) AS holders
WHERE distance_km <= %(radius_km)s
ORDER BY distance_km ASC
LIMIT %(limit)s
"""
    return db.fetch_all(sql, params)


def summary_by_sido() -> list[dict[str, Any]]:
    """시도별 응급실 가용 병상 집계 (대시보드용)."""
    sql = """
    SELECT
        COALESCE(h.sido, b.sido)                                   AS sido,
        count(*)                                                   AS hospital_count,
        count(*) FILTER (WHERE b.hvec > 0)                         AS available_count,
        COALESCE(sum(GREATEST(b.hvec, 0)), 0)                      AS total_beds,
        max(b.hvidate)                                             AS last_updated
    FROM bed_status_latest AS b
    LEFT JOIN hospitals AS h ON h.hpid = b.hpid
    GROUP BY COALESCE(h.sido, b.sido)
    ORDER BY sido NULLS LAST
    """
    return db.fetch_all(sql)


def pipeline_status() -> dict[str, Any]:
    """수집 파이프라인이 살아 있는지 확인하는 지표."""
    sql = """
    SELECT
        (SELECT count(*) FROM hospitals)          AS hospital_rows,
        (SELECT count(*) FROM bed_status_latest)  AS latest_rows,
        (SELECT count(*) FROM bed_status)         AS history_rows,
        (SELECT max(hvidate) FROM bed_status_latest)      AS last_hvidate,
        (SELECT max(collected_at) FROM bed_status_latest) AS last_collected_at
    """
    return db.fetch_one(sql) or {}
