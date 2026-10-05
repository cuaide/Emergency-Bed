
"""PostgreSQL 연결 풀과 UPSERT 헬퍼.

로컬 Docker PostgreSQL과 Azure Database for PostgreSQL 모두 같은 코드로 동작한다.
(차이는 PGHOST / PGSSLMODE 환경 변수뿐)
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()

# bed_status / bed_status_latest 공통 컬럼 (raw, collected_at 제외)
BED_COLUMNS: tuple[str, ...] = (
    "hpid",
    "hvidate",
    "duty_name",
    "sido",
    "hvec",
    "hvoc",
    "hvcc",
    "hvncc",
    "hvccc",
    "hvicc",
    "hvgc",
    "hvs01",
    # 세부 가용 병상 hv1 ~ hv12
    *(f"hv{index}" for index in range(1, 13)),
    "hvctayn",
    "hvmriayn",
    "hvangioayn",
    "hvventiayn",
    "hvamyn",
    "hvdnm",
)

HOSPITAL_COLUMNS: tuple[str, ...] = (
    "hpid",
    "duty_name",
    "duty_div_name",
    "duty_addr",
    "duty_tel1",
    "duty_tel3",
    "duty_emcls",
    "duty_emcls_name",
    "sido",
    "sigungu",
    "post_cdn",
    "latitude",
    "longitude",
    "nx",
    "ny",
)


def build_dsn(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return make_conninfo(
        host=settings.pg_host,
        port=settings.pg_port,
        dbname=settings.pg_database,
        user=settings.pg_user,
        password=settings.pg_password,
        sslmode=settings.pg_sslmode,
        connect_timeout=settings.pg_connect_timeout,
        application_name=settings.pg_application_name,
    )


def get_pool() -> ConnectionPool:
    """프로세스당 하나의 연결 풀을 지연 생성한다."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                settings = get_settings()
                _pool = ConnectionPool(
                    conninfo=build_dsn(settings),
                    min_size=settings.pg_pool_min,
                    max_size=settings.pg_pool_max,
                    kwargs={"row_factory": dict_row},
                    open=True,
                    name="nmc-pg-pool",
                )
                logger.info(
                    "PostgreSQL 연결 풀 생성 host=%s db=%s min=%d max=%d",
                    settings.pg_host,
                    settings.pg_database,
                    settings.pg_pool_min,
                    settings.pg_pool_max,
                )
    return _pool


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """트랜잭션 단위 커넥션. 블록을 정상 종료하면 커밋된다."""
    with get_pool().connection() as conn:
        yield conn


def init_schema() -> None:
    """schema.sql을 실행해 테이블을 생성한다 (IF NOT EXISTS)."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with connection() as conn:
        conn.execute(sql)
    logger.info("스키마 초기화 완료: %s", SCHEMA_PATH.name)


def ping() -> bool:
    try:
        with connection() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001 - 헬스체크는 실패 사유와 무관하게 False
        logger.exception("PostgreSQL ping 실패")
        return False


def _bed_params(row: dict[str, Any]) -> tuple[Any, ...]:
    values = [row.get(column) for column in BED_COLUMNS]
    values.append(Jsonb(row.get("raw") or {}))
    return tuple(values)


_BED_PLACEHOLDERS = ", ".join(["%s"] * (len(BED_COLUMNS) + 1))
_BED_COLUMN_LIST = ", ".join([*BED_COLUMNS, "raw"])
_BED_UPDATE_SET = ", ".join(
    f"{column} = EXCLUDED.{column}" for column in (*BED_COLUMNS[2:], "raw")
)

UPSERT_BED_STATUS_SQL = f"""
INSERT INTO bed_status ({_BED_COLUMN_LIST})
VALUES ({_BED_PLACEHOLDERS})
ON CONFLICT (hpid, hvidate) DO UPDATE SET
    {_BED_UPDATE_SET},
    collected_at = now()
"""

# 최신 스냅샷은 더 오래된 이벤트가 뒤늦게 도착해도 덮어쓰지 않는다.
UPSERT_BED_LATEST_SQL = f"""
INSERT INTO bed_status_latest ({_BED_COLUMN_LIST})
VALUES ({_BED_PLACEHOLDERS})
ON CONFLICT (hpid) DO UPDATE SET
    hvidate = EXCLUDED.hvidate,
    {_BED_UPDATE_SET},
    collected_at = now(),
    updated_at = now()
WHERE bed_status_latest.hvidate <= EXCLUDED.hvidate
"""


def upsert_bed_status(rows: Sequence[dict[str, Any]]) -> int:
    """실시간 병상 행을 bed_status(이력) + bed_status_latest(최신)에 UPSERT.

    반환값은 이력 테이블에 반영된 행 수.
    """
    if not rows:
        return 0

    params = [_bed_params(row) for row in rows]
    with connection() as conn, conn.cursor() as cur:
        cur.executemany(UPSERT_BED_STATUS_SQL, params)
        affected = cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(params)
        cur.executemany(UPSERT_BED_LATEST_SQL, params)
    logger.info("bed_status UPSERT %d건 (입력 %d건)", affected, len(rows))
    return affected


def _hospital_params(row: dict[str, Any]) -> tuple[Any, ...]:
    values = [row.get(column) for column in HOSPITAL_COLUMNS]
    values.append(Jsonb(row.get("raw") or {}))
    return tuple(values)


_HOSPITAL_PLACEHOLDERS = ", ".join(["%s"] * (len(HOSPITAL_COLUMNS) + 1))
_HOSPITAL_COLUMN_LIST = ", ".join([*HOSPITAL_COLUMNS, "raw"])
_HOSPITAL_UPDATE_SET = ", ".join(
    f"{column} = EXCLUDED.{column}" for column in (*HOSPITAL_COLUMNS[1:], "raw")
)

UPSERT_HOSPITAL_SQL = f"""
INSERT INTO hospitals ({_HOSPITAL_COLUMN_LIST})
VALUES ({_HOSPITAL_PLACEHOLDERS})
ON CONFLICT (hpid) DO UPDATE SET
    {_HOSPITAL_UPDATE_SET},
    updated_at = now()
"""


def upsert_hospitals(rows: Sequence[dict[str, Any]]) -> int:
    """기관 기본정보를 hospitals에 UPSERT."""
    if not rows:
        return 0

    params = [_hospital_params(row) for row in rows]
    with connection() as conn, conn.cursor() as cur:
        cur.executemany(UPSERT_HOSPITAL_SQL, params)
        affected = cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(params)
    logger.info("hospitals UPSERT %d건", affected)
    return affected


WEATHER_CURRENT_COLUMNS: tuple[str, ...] = (
    "nx",
    "ny",
    "base_datetime",
    "t1h",
    "rn1",
    "reh",
    "wsd",
    "vec",
    "pty",
)

WEATHER_FORECAST_COLUMNS: tuple[str, ...] = (
    "nx",
    "ny",
    "base_datetime",
    "fcst_datetime",
    "tmp",
    "reh",
    "wsd",
    "pop",
    "pty",
    "sky",
    "pcp",
)


def _build_upsert(table: str, columns: tuple[str, ...], key_count: int) -> str:
    """(columns[:key_count])를 키로 하는 UPSERT 문을 만든다. raw 컬럼이 항상 뒤에 붙는다."""
    placeholders = ", ".join(["%s"] * (len(columns) + 1))
    column_list = ", ".join([*columns, "raw"])
    keys = ", ".join(columns[:key_count])
    update_set = ", ".join(
        f"{column} = EXCLUDED.{column}" for column in (*columns[key_count:], "raw")
    )
    return f"""
INSERT INTO {table} ({column_list})
VALUES ({placeholders})
ON CONFLICT ({keys}) DO UPDATE SET
    {update_set},
    collected_at = now()
"""


UPSERT_WEATHER_CURRENT_SQL = _build_upsert("weather_current", WEATHER_CURRENT_COLUMNS, 3)
UPSERT_WEATHER_FORECAST_SQL = _build_upsert("weather_forecast", WEATHER_FORECAST_COLUMNS, 4)


def _row_params(row: dict[str, Any], columns: tuple[str, ...]) -> tuple[Any, ...]:
    values = [row.get(column) for column in columns]
    values.append(Jsonb(row.get("raw") or {}))
    return tuple(values)


def _upsert_many(sql: str, columns: tuple[str, ...], rows: Sequence[dict[str, Any]]) -> int:
    if not rows:
        return 0
    params = [_row_params(row, columns) for row in rows]
    with connection() as conn, conn.cursor() as cur:
        cur.executemany(sql, params)
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(params)


def upsert_weather_current(rows: Sequence[dict[str, Any]]) -> int:
    """초단기실황을 weather_current에 UPSERT."""
    affected = _upsert_many(UPSERT_WEATHER_CURRENT_SQL, WEATHER_CURRENT_COLUMNS, rows)
    logger.info("weather_current UPSERT %d건", affected)
    return affected


def upsert_weather_forecast(rows: Sequence[dict[str, Any]]) -> int:
    """단기예보를 weather_forecast에 UPSERT."""
    affected = _upsert_many(UPSERT_WEATHER_FORECAST_SQL, WEATHER_FORECAST_COLUMNS, rows)
    logger.info("weather_forecast UPSERT %d건", affected)
    return affected


def fetch_all(sql: str, params: dict[str, Any] | Sequence[Any] | None = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(sql: str, params: dict[str, Any] | Sequence[Any] | None = None) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()
