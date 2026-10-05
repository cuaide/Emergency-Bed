from __future__ import annotations

import argparse
import sys

from app.services.tmap import TmapError, fetch_route, search_poi


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-lat", type=float, required=True)
    parser.add_argument("--start-lon", type=float, required=True)
    parser.add_argument("--dest", help="목적지 장소명 (POI 검색으로 좌표를 찾는다)")
    parser.add_argument("--dest-lat", type=float, help="목적지 위도 (좌표를 직접 아는 경우)")
    parser.add_argument("--dest-lon", type=float, help="목적지 경도")
    args = parser.parse_args()

    if args.dest_lat is not None and args.dest_lon is not None:
        end_lat, end_lon, dest_name = args.dest_lat, args.dest_lon, "지정 좌표"
    elif args.dest:
        poi = search_poi(args.dest)
        end_lat, end_lon, dest_name = poi["latitude"], poi["longitude"], poi["name"]
        print(f"목적지 검색: '{args.dest}' → {dest_name} ({end_lat}, {end_lon})")
    else:
        parser.error("--dest 또는 --dest-lat/--dest-lon 중 하나는 필요합니다.")
        return 2

    route = fetch_route(
        start_latitude=args.start_lat,
        start_longitude=args.start_lon,
        end_latitude=end_lat,
        end_longitude=end_lon,
    )

    print(f"{dest_name}까지 {route['distance_km']}km, {route['duration_min']}분")
    print(f"전체 혼잡도: {route['congestion_label']} (level={route['congestion_level']})")
    if route["jammed_segments"]:
        print("막히는 구간:")
        for seg in route["jammed_segments"]:
            print(
                f"  - {seg['road_name']}: {seg['congestion_label']}"
                f" ({seg['distance_m']}m, {seg['speed_kmh']}km/h)"
            )
    else:
        print("막히는 구간 없음")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except TmapError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        sys.exit(1)
