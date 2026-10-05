from __future__ import annotations
import argparse
import json
import logging
import sys

from app.collectors.emergency import (
    EmergencyApiError, 
    fetch_bed_status, 
    fetch_hospitals, 
    save_bed_status, 
    save_hospitals
)
from app.eventhub import send_to_event_hub 

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s") 


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="응급 병상 수집 CLI")
    parser.add_argument("--districts", help="쉼표로 구분한 시도명 (기본: 전국)")
    parser.add_argument("--to-eventhub", action="store_true", help="DB 대신 Event Hub로 전송") 
    parser.add_argument("--dry-run", action="store_true", help="저장하지 않고 결과만 출력")
    parser.add_argument("--hospitals", action="store_true", help="기관 기본정보 수집")
    parser.add_argument("--limit", type=int, default=3, help="--dry-run 시 출력할 건수") 
    args = parser.parse_args(argv)  

    districts = [d.strip() for d in args.districts.split(",")] if args.districts else None

    fetch = fetch_hospitals if args.hospitals else fetch_bed_status
    try:
        rows = fetch(districts)
    except EmergencyApiError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    print(f"수집 {len(rows)}건")

    if args.dry_run:
        for row in rows[: args.limit]:
            print(json.dumps(row, ensure_ascii=False, default=str, indent=2))
        return 0

    if args.to_eventhub:
        if args.hospitals:
            parser.error("--hospitals 는 Event Hub 경로를 사용하지 않습니다.")
        sent = send_to_event_hub(rows) 
        print(f"Event Hub 전송 {sent}건")
        return 0

    saved = save_hospitals(rows) if args.hospitals else save_bed_status(rows) 
    print(f"DB 적재 {saved}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
