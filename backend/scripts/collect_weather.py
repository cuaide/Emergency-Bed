from __future__ import annotations

import argparse
import json
import logging
import sys

from app.collectors.weather import (
    WeatherApiError,
    backfill_hospital_grids,
    fetch_weather_current,
    fetch_weather_forecast,
    save_weather_current,
    save_weather_forecast, 
)
from app.grid import Grid, latlon_to_grid

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")


def _parse_grids(args: argparse.Namespace) -> list[Grid] | None:
    grids: list[Grid] = []
    for text in args.grid or []:
        nx, ny = (part.strip() for part in text.split(","))
        grids.append(Grid(int(nx), int(ny)))
    for text in args.latlon or []:
        lat, lon = (float(part.strip()) for part in text.split(","))
        grids.append(latlon_to_grid(lat, lon))
    return grids or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="기상청 단기예보 수집 CLI")
    parser.add_argument("--grid", action="append", help="격자 'nx,ny' (여러 번 지정 가능)")
    parser.add_argument("--latlon", action="append", help="위경도 'lat,lon' (격자로 변환)")
    parser.add_argument(
        "--only", choices=("current", "forecast"), help="한 종류만 수집 (기본: 둘 다)"
    )
    parser.add_argument("--dry-run", action="store_true", help="저장하지 않고 결과만 출력")
    parser.add_argument("--limit", type=int, default=3, help="--dry-run 시 출력할 건수")
    parser.add_argument(
        "--backfill-grids", action="store_true", help="병원 격자(nx, ny)만 채우고 종료"
    )
    args = parser.parse_args(argv)

    if args.backfill_grids:
        print(f"병원 격자 보정 {backfill_hospital_grids()}건")
        return 0

    grids = _parse_grids(args)
    if grids:
        print(f"대상 격자: {[tuple(g) for g in grids]}")

    total = 0
    for kind in ("current", "forecast"):
        if args.only and args.only != kind:
            continue

        is_current = kind == "current"
        fetch = fetch_weather_current if is_current else fetch_weather_forecast
        try:
            rows = fetch(grids)
        except WeatherApiError as exc:
            # 격자 없음 / 키 없음 / API 오류 모두 트레이스백 없이 한 줄로 알린다.
            print(f"오류: {exc}", file=sys.stderr)
            return 1
        print(f"[{kind}] 수집 {len(rows)}건")

        if args.dry_run:
            for row in rows[: args.limit]:
                print(json.dumps(row, ensure_ascii=False, default=str, indent=2))
            continue

        saved = save_weather_current(rows) if is_current else save_weather_forecast(rows)
        print(f"[{kind}] DB 적재 {saved}건")
        total += saved

    if not args.dry_run:
        print(f"합계 {total}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
