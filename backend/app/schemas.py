"""FastAPI 응답 스키마 (Pydantic v2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BedStatus(BaseModel):
    """bed_status_latest + hospitals 조인 결과 1건."""

    model_config = ConfigDict(from_attributes=True)

    hpid: str = Field(description="응급의료기관 코드")
    duty_name: str | None = Field(default=None, description="기관명")
    sido: str | None = None
    sigungu: str | None = None
    duty_addr: str | None = Field(default=None, description="주소")
    duty_tel3: str | None = Field(default=None, description="응급실 전화번호")
    duty_emcls_name: str | None = Field(default=None, description="응급의료기관 분류")
    latitude: float | None = None
    longitude: float | None = None

    hvidate: datetime = Field(description="병원이 입력한 정보 갱신 시각")
    hvec: int | None = Field(default=None, description="응급실 일반 가용 병상")
    hvoc: int | None = Field(default=None, description="수술실 가용")
    hvcc: int | None = Field(default=None, description="신경외과 중환자실 가용")
    hvncc: int | None = Field(default=None, description="신생아 중환자실 가용")
    hvccc: int | None = Field(default=None, description="흉부외과 중환자실 가용")
    hvicc: int | None = Field(default=None, description="일반 중환자실 가용")
    hvgc: int | None = Field(default=None, description="입원실 가용")
    hvs01: int | None = Field(default=None, description="응급실 기준 병상 수")

    # 세부 가용 병상 hv1 ~ hv12 (원본 값은 raw JSONB에도 보존)
    hv1: int | None = None
    hv2: int | None = None
    hv3: int | None = None
    hv4: int | None = None
    hv5: int | None = None
    hv6: int | None = None
    hv7: int | None = None
    hv8: int | None = None
    hv9: int | None = None
    hv10: int | None = None
    hv11: int | None = None
    hv12: int | None = None

    hvctayn: bool | None = Field(default=None, description="CT 가용")
    hvmriayn: bool | None = Field(default=None, description="MRI 가용")
    hvangioayn: bool | None = Field(default=None, description="혈관촬영기 가용")
    hvventiayn: bool | None = Field(default=None, description="인공호흡기 가용")
    hvamyn: bool | None = Field(default=None, description="구급차 가용")
    hvdnm: str | None = Field(default=None, description="당직의")

    collected_at: datetime | None = None
    updated_at: datetime | None = None
    is_stale: bool = Field(default=False, description="갱신이 지연된 데이터인지")


class BedStatusNearby(BedStatus):
    distance_km: float = Field(description="요청 좌표로부터의 직선 거리(km)")


class CongestionSegment(BaseModel):
    """지체(3) 이상인 혼잡 구간 하나."""

    road_name: str
    distance_m: int | None = None
    congestion: int = Field(description="0 정보없음 · 1 원활 · 2 서행 · 3 지체 · 4 정체")
    congestion_label: str
    speed_kmh: float | None = None


class CongestionSpan(BaseModel):
    """path 배열의 [start, end] 구간에 적용되는 혼잡도. 지도에 색으로 그린다."""

    start: int
    end: int
    congestion: int = Field(description="0 정보없음 · 1 원활 · 2 서행 · 3 지체 · 4 정체")


class RouteSummary(BaseModel):
    """Tmap 자동차 경로 요약 (실시간 교통 반영)."""

    path: list[list[float]] = Field(
        default_factory=list, description="경로 좌표 [[경도, 위도], ...]"
    )
    path_congestion: list[CongestionSpan] = Field(
        default_factory=list, description="path 인덱스 기준 구간별 혼잡도"
    )
    distance_m: int = Field(description="총 거리(m)")
    distance_km: float = Field(description="총 거리(km)")
    duration_s: int = Field(description="예상 소요시간(초)")
    duration_min: float = Field(description="예상 소요시간(분)")
    taxi_fare: int | None = Field(default=None, description="예상 택시요금(원)")
    toll_fare: int | None = Field(default=None, description="예상 통행료(원)")
    congestion_level: int = Field(default=0, description="경로 전체 중 최고 혼잡도 (0~4)")
    congestion_label: str = Field(default="정보없음", description="congestion_level 한글 표기")
    jammed_segments: list[CongestionSegment] = Field(
        default_factory=list, description="지체(3) 이상 구간만"
    )


class BedStatusRoute(BedStatusNearby):
    route: RouteSummary | None = Field(
        default=None, description="경로 조회 실패 시 null (해당 후보는 목록 뒤로 밀림)"
    )


class BedStatusList(BaseModel):
    total: int
    count: int
    items: list[BedStatus]


class BedHistoryPoint(BaseModel):
    hpid: str
    hvidate: datetime
    hvec: int | None = None
    hvoc: int | None = None
    hvicc: int | None = None
    hvgc: int | None = None
    hvs01: int | None = None
    collected_at: datetime | None = None


class SidoSummary(BaseModel):
    sido: str | None = None
    hospital_count: int
    available_count: int
    total_beds: int
    last_updated: datetime | None = None


class PipelineStatus(BaseModel):
    hospital_rows: int
    latest_rows: int
    history_rows: int
    last_hvidate: datetime | None = None
    last_collected_at: datetime | None = None


class HealthResponse(BaseModel):
    status: str
    database: str


class MapConfig(BaseModel):
    """브라우저 지도 SDK가 쓰는 공개 설정."""

    tmap_app_key: str = Field(description="Tmap 지도 SDK appKey")


class PlaceResult(BaseModel):
    """장소명/주소 → 좌표 (Tmap POI 검색 첫 결과)."""

    name: str | None = Field(default=None, description="검색된 장소명")
    latitude: float
    longitude: float


class WeatherCurrent(BaseModel):
    """기상청 초단기실황."""

    nx: int
    ny: int
    base_datetime: datetime = Field(description="관측 발표 시각")
    t1h: float | None = Field(default=None, description="기온(°C)")
    rn1: float | None = Field(default=None, description="1시간 강수량(mm)")
    reh: float | None = Field(default=None, description="습도(%)")
    wsd: float | None = Field(default=None, description="풍속(m/s)")
    vec: float | None = Field(default=None, description="풍향(deg)")
    pty: int | None = Field(default=None, description="강수형태 코드")
    pty_label: str | None = Field(default=None, description="강수형태(한글)")
    collected_at: datetime | None = None


class WeatherForecastPoint(BaseModel):
    """기상청 단기예보 1시간 단위."""

    nx: int
    ny: int
    base_datetime: datetime = Field(description="예보 발표 시각")
    fcst_datetime: datetime = Field(description="예보 대상 시각")
    tmp: float | None = Field(default=None, description="기온(°C)")
    reh: float | None = Field(default=None, description="습도(%)")
    wsd: float | None = Field(default=None, description="풍속(m/s)")
    pop: int | None = Field(default=None, description="강수확률(%)")
    pty: int | None = Field(default=None, description="강수형태 코드")
    pty_label: str | None = None
    sky: int | None = Field(default=None, description="하늘상태 코드")
    sky_label: str | None = None
    pcp: str | None = Field(default=None, description="1시간 강수량")
    collected_at: datetime | None = None


class WeatherResponse(BaseModel):
    nx: int
    ny: int
    current: WeatherCurrent | None = None
    forecast: list[WeatherForecastPoint] = Field(default_factory=list)


class WeatherCoverage(BaseModel):
    current_grids: int
    current_rows: int
    current_base: datetime | None = None
    forecast_grids: int
    forecast_rows: int
    forecast_base: datetime | None = None


class TriageRequest(BaseModel):
    """텍스트로 증상을 입력받는 요청."""

    message: str = Field(min_length=1, max_length=2000, description="증상 설명")
    generate_audio: bool = Field(default=True, description="안내 음성(WAV) 생성 여부")


class TriageResponse(BaseModel):
    """증상 분류 결과.

    triage의 내부 구조는 Foundry 에이전트가 정하므로 여기서 고정하지 않는다.
    에이전트 프롬프트를 바꿀 때마다 스키마를 따라 고치는 일을 피하기 위해서다.
    """

    recognized_text: str | None = Field(
        default=None, description="STT로 인식된 문장 (음성 입력일 때만)"
    )
    triage: dict[str, Any] = Field(description="에이전트 분류 결과")
    audio_url: str | None = Field(
        default=None, description="안내 음성 경로. 합성 실패 시 null"
    )
