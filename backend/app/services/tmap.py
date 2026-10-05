from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any

import requests

from app.config import get_settings

logger = logging.getLogger(__name__)

ROUTE_PATH = "/tmap/routes"
POI_PATH = "/tmap/pois"
ROUTE_CACHE_TTL_S = 120
ROUTE_CACHE_SIZE = 512
COORD_PRECISION = 4

SEARCH_OPTION_RECOMMENDED = "0"
CONGESTION_LABELS: dict[int, str] = {
    0: "정보없음",
    1: "원활",
    2: "서행",
    3: "지체",
    4: "정체",
}
JAMMED_THRESHOLD = 3  


class TmapError(RuntimeError):
    """Tmap 호출이 실패했거나 앱 키가 없는 경우."""


class TmapQuotaError(TmapError):
    """앱 키의 호출 한도를 다 쓴 경우(HTTP 429).

    다른 실패와 달리 재시도해도 소용이 없고, 한도가 풀릴 때까지 모든 후보가 똑같이
    실패한다. 조용히 직선거리로 내려가면 "경로가 왜 안 나오는지" 알 길이 없어서
    별도 타입으로 구분해 화면까지 올린다.
    """


def is_configured() -> bool:
    return bool(get_settings().tmap_app_key)


def fetch_route(
    *,
    start_latitude: float,
    start_longitude: float,
    end_latitude: float,
    end_longitude: float,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    if session is not None:
        return _request_route(
            start_latitude, start_longitude, end_latitude, end_longitude, session
        )
    return _cached_route(
        int(time.time() // ROUTE_CACHE_TTL_S),
        round(start_latitude, COORD_PRECISION),
        round(start_longitude, COORD_PRECISION),
        round(end_latitude, COORD_PRECISION),
        round(end_longitude, COORD_PRECISION),
    )

@lru_cache(maxsize=ROUTE_CACHE_SIZE)
def _cached_route(
    _bucket: int,
    start_latitude: float,
    start_longitude: float,
    end_latitude: float,
    end_longitude: float,
) -> dict[str, Any]:
    return _request_route(start_latitude, start_longitude, end_latitude, end_longitude, None)


def _request_route(
    start_latitude: float,
    start_longitude: float,
    end_latitude: float,
    end_longitude: float,
    session: requests.Session | None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.tmap_app_key:
        raise TmapError("TMAP_APP_KEY 환경 변수가 비어 있습니다.")

    url = f"{settings.tmap_base_url.rstrip('/')}{ROUTE_PATH}"
    payload = {
        "startX": start_longitude,
        "startY": start_latitude,
        "endX": end_longitude,
        "endY": end_latitude,
        "reqCoordType": "WGS84GEO",
        "resCoordType": "WGS84GEO",
        "searchOption": SEARCH_OPTION_RECOMMENDED,
        "trafficInfo": "Y",
    }
    headers = {"appKey": settings.tmap_app_key, "Content-Type": "application/json"}

    owns_session = session is None
    session = session or requests.Session()
    try:
        response = session.post(
            url,
            params={"version": 1, "format": "json"},
            json=payload,
            headers=headers,
            timeout=settings.tmap_timeout,
        )
        if response.status_code == 429:
            raise TmapQuotaError(
                "Tmap 경로 호출 한도를 초과했습니다 (QUOTA_EXCEEDED). "
                "한도가 초기화되거나 다른 앱 키를 쓸 때까지 실제 이동시간을 계산할 수 없습니다."
            )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise TmapError(f"Tmap 경로 조회 실패: {exc}") from exc
    finally:
        if owns_session:
            session.close()

    return parse_route(body)


def _congestion_segments(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """LineString 구간의 geometry.traffic (각 원소 [시작idx, 끝idx, 혼잡도, 속도])을 펼친다."""
    segments: list[dict[str, Any]] = []
    for feature in features:
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "LineString":
            continue
        traffic = geometry.get("traffic") or []
        if not traffic:
            continue
        properties = feature.get("properties") or {}
        road_name = properties.get("name") or properties.get("description") or "이름 없음"
        for quad in traffic:
            if len(quad) < 4:
                continue
            _, _, congestion, speed = quad[:4]
            segments.append(
                {
                    "road_name": road_name,
                    "distance_m": properties.get("distance"),
                    "congestion": congestion,
                    "congestion_label": CONGESTION_LABELS.get(congestion, "알 수 없음"),
                    "speed_kmh": speed,
                }
            )
    return segments


def _route_path(features: list[dict[str, Any]]) -> tuple[list[list[float]], list[dict[str, Any]]]:
    points: list[list[float]] = []
    spans: list[dict[str, Any]] = []
    for feature in features:
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "LineString":
            continue
        coords = [c for c in (geometry.get("coordinates") or []) if len(c) >= 2]
        if not coords:
            continue
        base = len(points)
        points.extend([round(float(c[0]), 6), round(float(c[1]), 6)] for c in coords)
        for quad in geometry.get("traffic") or []:
            if len(quad) < 4:
                continue
            start, end, congestion = int(quad[0]), int(quad[1]), int(quad[2])
            start = max(0, min(start, len(coords) - 1))
            end = max(start, min(end, len(coords) - 1))
            spans.append({"start": base + start, "end": base + end, "congestion": congestion})
    return points, spans


def parse_route(body: dict[str, Any]) -> dict[str, Any]:
    features = body.get("features") or []
    if not features:
        raise TmapError("Tmap 응답에 경로가 없습니다.")

    properties = features[0].get("properties") or {}
    distance_m = properties.get("totalDistance")
    duration_s = properties.get("totalTime")
    if distance_m is None or duration_s is None:
        raise TmapError("Tmap 응답에 거리/시간이 없습니다.")

    distance_m = int(distance_m)
    duration_s = int(duration_s)

    segments = _congestion_segments(features)
    jammed = [s for s in segments if s["congestion"] >= JAMMED_THRESHOLD]
    congestion_level = max((s["congestion"] for s in segments), default=0)
    path, path_congestion = _route_path(features)

    return {
        "path": path,
        "path_congestion": path_congestion,
        "distance_m": distance_m,
        "distance_km": round(distance_m / 1000, 2),
        "duration_s": duration_s,
        "duration_min": round(duration_s / 60, 1),
        "taxi_fare": properties.get("taxiFare"),
        "toll_fare": properties.get("totalFare"),
        "congestion_level": congestion_level,
        "congestion_label": CONGESTION_LABELS.get(congestion_level, "정보없음"),
        "jammed_segments": jammed,
    }


def search_poi(keyword: str, *, session: requests.Session | None = None) -> dict[str, Any]:
    settings = get_settings()
    if not settings.tmap_app_key:
        raise TmapError("TMAP_APP_KEY 환경 변수가 비어 있습니다.")

    url = f"{settings.tmap_base_url.rstrip('/')}{POI_PATH}"
    headers = {"appKey": settings.tmap_app_key, "Accept": "application/json"}
    params = {
        "version": 1,
        "searchKeyword": keyword,
        "count": 1,
        "resCoordType": "WGS84GEO",
        "reqCoordType": "WGS84GEO",
    }

    owns_session = session is None
    session = session or requests.Session()
    try:
        response = session.get(url, params=params, headers=headers, timeout=settings.tmap_timeout)
        if response.status_code == 429:
            raise TmapQuotaError("Tmap 장소 검색 한도를 초과했습니다 (QUOTA_EXCEEDED).")
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise TmapError(f"Tmap POI 검색 실패: {exc}") from exc
    finally:
        if owns_session:
            session.close()

    return parse_poi(body, keyword)


def parse_poi(body: dict[str, Any], keyword: str) -> dict[str, Any]:
    pois = ((body.get("searchPoiInfo") or {}).get("pois") or {}).get("poi") or []
    if not pois:
        raise TmapError(f"'{keyword}'에 대한 검색 결과가 없습니다.")

    first = pois[0]
    return {
        "name": first.get("name"),
        "latitude": float(first["noorLat"]),
        "longitude": float(first["noorLon"]),
    }


def attach_routes(
    candidates: list[dict[str, Any]],
    *,
    start_latitude: float,
    start_longitude: float,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    settings = get_settings()
    max_candidates = limit or settings.tmap_max_candidates
    targets = candidates[:max_candidates]

    if not targets:
        return []
    if not settings.tmap_app_key:
        raise TmapError("TMAP_APP_KEY 환경 변수가 비어 있습니다.")

    def _one(candidate: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(candidate)
        latitude, longitude = candidate.get("latitude"), candidate.get("longitude")
        if latitude is None or longitude is None:
            enriched["route"] = None
            return enriched
        try:
            enriched["route"] = fetch_route(
                start_latitude=start_latitude,
                start_longitude=start_longitude,
                end_latitude=latitude,
                end_longitude=longitude,
            )
        except TmapQuotaError:
            raise
        except TmapError as exc:
            logger.warning("hpid=%s 경로 조회 실패: %s", candidate.get("hpid"), exc)
            enriched["route"] = None
        return enriched

    workers = max(1, min(settings.tmap_concurrency, len(targets)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        enriched = list(pool.map(_one, targets))

    def _sort_key(item: dict[str, Any]) -> tuple[int, float]:
        route = item.get("route")
        if not route:
            return (1, item.get("distance_km") or float("inf"))
        return (0, route["duration_s"])

    enriched.sort(key=_sort_key)
    return enriched
