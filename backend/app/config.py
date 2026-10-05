from __future__ import annotations
import os
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

from dotenv import load_dotenv


load_dotenv()


DEFAULT_API_BASE_URL = "http://apis.data.go.kr/B552657/ErmctInfoInqireService"
DEFAULT_WEATHER_BASE_URL = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"
DEFAULT_TMAP_BASE_URL = "https://apis.openapi.sk.com"
DEFAULT_AUDIO_DIR = str(Path(tempfile.gettempdir()) / "nmc-audio")
DEFAULT_DISTRICTS: tuple[str, ...] = ("서울특별시",)

def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value

def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default

def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default

def _env_bool(name: str, default: bool = False) -> bool:
    return _env(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}

def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = _env(name)
    if not raw:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())

@dataclass(frozen=True)
class Settings:
    # --- 공공 API ---
    service_key: str = ""
    api_base_url: str = DEFAULT_API_BASE_URL
    api_timeout: float = 10.0
    api_num_of_rows: int = 100
    api_max_pages: int = 50
    api_retry: int = 2
    districts: tuple[str, ...] = field(default_factory=lambda: DEFAULT_DISTRICTS)

    # --- PostgreSQL ---
    pg_host: str = ""
    pg_port: int = 5432
    pg_database: str = "nmc_db"
    pg_user: str = "postgres"
    pg_password: str = ""
    pg_sslmode: str = "prefer"
    pg_connect_timeout: int = 10
    pg_pool_min: int = 1
    pg_pool_max: int = 5
    pg_application_name: str = "nmc-bed-pipeline"

    # --- Event Hub ---
    event_hub_name: str = "nmc-bed-status"
    event_hub_connection: str = ""
    event_hub_consumer_group: str = "$Default"

    # --- 기상청 단기예보 ---
    # data.go.kr에서 API별로 별도 승인된 키를 받는 계정이 있어, 기본은 응급의료 키와
    
    weather_service_key: str = ""
    weather_base_url: str = DEFAULT_WEATHER_BASE_URL
    weather_max_grids: int = 200
    weather_forecast_hours: int = 24

    # --- Tmap ---
    tmap_app_key: str = ""
    tmap_base_url: str = DEFAULT_TMAP_BASE_URL
    tmap_timeout: float = 5.0
    tmap_max_candidates: int = 5
    tmap_concurrency: int = 5

    # --- Azure AI Speech (STT / TTS) ---
    speech_key: str = ""
    speech_region: str = "koreacentral"
    speech_language: str = "ko-KR"
    speech_voice: str = "ko-KR-SunHiNeural"
    audio_dir: str = DEFAULT_AUDIO_DIR
    audio_ttl_minutes: int = 30

    # --- Azure AI Foundry (증상 분류 에이전트) ---
    project_endpoint: str = ""
    agent_name: str = ""
    agent_version: str = "1"

    # --- 동작 옵션 ---
    auto_init_db: bool = False
    stale_after_minutes: int = 90
    cors_origins: tuple[str, ...] = ("*",)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            service_key=_env("DATA_GO_KR_SERVICE_KEY"),
            api_base_url=_env("DATA_GO_KR_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/"),
            api_timeout=_env_float("DATA_GO_KR_TIMEOUT", 10.0),
            api_num_of_rows=_env_int("DATA_GO_KR_NUM_OF_ROWS", 100),
            api_max_pages=_env_int("DATA_GO_KR_MAX_PAGES", 50),
            api_retry=_env_int("DATA_GO_KR_RETRY", 2),
            districts=_env_list("BED_COLLECT_DISTRICTS", DEFAULT_DISTRICTS),
            pg_host=_env("PGHOST", "localhost"),
            pg_port=_env_int("PGPORT", 5432),
            pg_database=_env("PGDATABASE", "nmc_db"),
            pg_user=_env("PGUSER", "postgres"),
            pg_password=_env("PGPASSWORD"),
            pg_sslmode=_env("PGSSLMODE", "prefer"),
            pg_connect_timeout=_env_int("PGCONNECT_TIMEOUT", 10),
            pg_pool_min=_env_int("PG_POOL_MIN_SIZE", 1),
            pg_pool_max=_env_int("PG_POOL_MAX_SIZE", 5),
            pg_application_name=_env("PG_APPLICATION_NAME", "nmc-bed-pipeline"),
            event_hub_name=_env("BED_EVENT_HUB_NAME", "nmc-bed-status"),
            event_hub_connection=_env("EventHubConnection"),
            event_hub_consumer_group=_env("BED_EVENT_HUB_CONSUMER_GROUP", "$Default"),
            weather_service_key=unquote(_env("WEATHER_API_KEY")) or _env("DATA_GO_KR_SERVICE_KEY"),
            weather_base_url=_env("WEATHER_BASE_URL", DEFAULT_WEATHER_BASE_URL).rstrip("/"),
            weather_max_grids=_env_int("WEATHER_MAX_GRIDS", 200),
            weather_forecast_hours=_env_int("WEATHER_FORECAST_HOURS", 24),
            tmap_app_key=_env("TMAP_APP_KEY"),
            tmap_base_url=_env("TMAP_BASE_URL", DEFAULT_TMAP_BASE_URL),
            tmap_timeout=_env_float("TMAP_TIMEOUT", 5.0),
            tmap_max_candidates=_env_int("TMAP_MAX_CANDIDATES", 5),
            tmap_concurrency=_env_int("TMAP_CONCURRENCY", 5),
            speech_key=_env("SPEECH_KEY"),
            speech_region=_env("SPEECH_REGION", "koreacentral"),
            speech_language=_env("SPEECH_LANGUAGE", "ko-KR"),
            speech_voice=_env("SPEECH_VOICE", "ko-KR-SunHiNeural"),
            audio_dir=_env("AUDIO_DIR", DEFAULT_AUDIO_DIR),
            audio_ttl_minutes=_env_int("AUDIO_TTL_MINUTES", 30),
            project_endpoint=_env("PROJECT_ENDPOINT").rstrip("/"),
            agent_name=_env("AGENT_NAME"),
            agent_version=_env("AGENT_VERSION", "1"),
            auto_init_db=_env_bool("AUTO_INIT_DB", False),
            stale_after_minutes=_env_int("BED_STALE_AFTER_MINUTES", 90),
            cors_origins=_env_list("CORS_ORIGINS", ("*",)),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def reset_settings_cache() -> None:
    """테스트에서 환경 변수를 바꾼 뒤 호출."""
    get_settings.cache_clear()
