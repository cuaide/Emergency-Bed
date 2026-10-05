
"""테이블 생성 스크립트.
    python -m scripts.init_db
"""

from __future__ import annotations

import logging
import sys

from app import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")


def main() -> int:
    db.init_schema()
    status = db.fetch_one(
        """
        SELECT
            (SELECT count(*) FROM hospitals)         AS hospitals,
            (SELECT count(*) FROM bed_status)        AS bed_status,
            (SELECT count(*) FROM bed_status_latest) AS bed_status_latest
        """
    )
    print(f"테이블 준비 완료: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
