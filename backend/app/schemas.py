from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BedStatus(BaseModel):
    """One row of the bed_status_latest + hospitals join."""

    model_config = ConfigDict(from_attributes=True)

    hpid: str = Field(description="Emergency medical institution code")
    duty_name: str | None = Field(default=None, description="Institution name")
    sido: str | None = None
    sigungu: str | None = None
    duty_addr: str | None = Field(default=None, description="Address")
    duty_tel3: str | None = Field(default=None, description="ER phone number")
    duty_emcls_name: str | None = Field(default=None, description="Emergency institution classification")
    latitude: float | None = None
    longitude: float | None = None

    hvidate: datetime = Field(description="Time the hospital last updated the information")
    hvec: int | None = Field(default=None, description="Available ER general beds")
    hvoc: int | None = Field(default=None, description="Available operating rooms")
    hvcc: int | None = Field(default=None, description="Available neurosurgical ICU beds")
    hvncc: int | None = Field(default=None, description="Available neonatal ICU beds")
    hvccc: int | None = Field(default=None, description="Available thoracic surgery ICU beds")
    hvicc: int | None = Field(default=None, description="Available general ICU beds")
    hvgc: int | None = Field(default=None, description="Available inpatient beds")
    hvs01: int | None = Field(default=None, description="ER reference bed count")

    # Detailed available beds hv1 ~ hv12 (original values are also preserved in raw JSONB)
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

    hvctayn: bool | None = Field(default=None, description="CT available")
    hvmriayn: bool | None = Field(default=None, description="MRI available")
    hvangioayn: bool | None = Field(default=None, description="Angiography available")
    hvventiayn: bool | None = Field(default=None, description="Ventilator available")
    hvamyn: bool | None = Field(default=None, description="Ambulance available")
    hvdnm: str | None = Field(default=None, description="On-duty doctor")

    collected_at: datetime | None = None
    updated_at: datetime | None = None
    is_stale: bool = Field(default=False, description="Whether the data's update is delayed")


class BedStatusNearby(BedStatus):
    distance_km: float = Field(description="Straight-line distance from the requested coordinates (km)")


class CongestionSegment(BaseModel):
    """A single congested segment at level 3 (delayed) or above."""

    road_name: str
    distance_m: int | None = None
    congestion: int = Field(description="0 no info · 1 smooth · 2 slow · 3 delayed · 4 jammed")
    congestion_label: str
    speed_kmh: float | None = None


class CongestionSpan(BaseModel):
    """Congestion level applied to the [start, end] range of the path array. Drawn as colors on the map."""

    start: int
    end: int
    congestion: int = Field(description="0 no info · 1 smooth · 2 slow · 3 delayed · 4 jammed")


class RouteSummary(BaseModel):
    """Tmap car route summary (reflects real-time traffic)."""

    path: list[list[float]] = Field(default_factory=list, description="Route coordinates [[lon, lat], ...]")
    path_congestion: list[CongestionSpan] = Field(default_factory=list, description="Per-segment congestion, indexed by path")
    distance_m: int = Field(description="Total distance (m)")
    distance_km: float = Field(description="Total distance (km)")
    duration_s: int = Field(description="Estimated travel time (seconds)")
    duration_min: float = Field(description="Estimated travel time (minutes)")
    taxi_fare: int | None = Field(default=None, description="Estimated taxi fare (KRW)")
    toll_fare: int | None = Field(default=None, description="Estimated toll (KRW)")
    congestion_level: int = Field(default=0, description="Highest congestion level along the whole route (0~4)")
    congestion_label: str = Field(default="정보없음", description="Korean label for congestion_level")
    jammed_segments: list[CongestionSegment] = Field(default_factory=list, description="Only segments at level 3 (delayed) or above")


class BedStatusRoute(BedStatusNearby):
    route: RouteSummary | None = Field(default=None, description="null if the route lookup failed (that candidate is pushed to the end of the list)")


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
    tmap_app_key: str = Field(description="Tmap map SDK appKey")

# Place name/address → coordinates (first result of Tmap POI search)
class PlaceResult(BaseModel):


    name: str | None = Field(default=None, description="Name of the place found")
    latitude: float
    longitude: float

# KMA ultra-short-term observations
class WeatherCurrent(BaseModel):
    

    nx: int
    ny: int
    base_datetime: datetime = Field(description="Observation release time")
    t1h: float | None = Field(default=None, description="Temperature (°C)")
    rn1: float | None = Field(default=None, description="1-hour precipitation (mm)")
    reh: float | None = Field(default=None, description="Humidity (%)")
    wsd: float | None = Field(default=None, description="Wind speed (m/s)")
    vec: float | None = Field(default=None, description="Wind direction (deg)")
    pty: int | None = Field(default=None, description="Precipitation type code")
    pty_label: str | None = Field(default=None, description="Precipitation type (Korean label)")
    collected_at: datetime | None = None

# KMA short-term forecast, hourly
class WeatherForecastPoint(BaseModel):


    nx: int
    ny: int
    base_datetime: datetime = Field(description="Forecast release time")
    fcst_datetime: datetime = Field(description="Forecast target time")
    tmp: float | None = Field(default=None, description="Temperature (°C)")
    reh: float | None = Field(default=None, description="Humidity (%)")
    wsd: float | None = Field(default=None, description="Wind speed (m/s)")
    pop: int | None = Field(default=None, description="Probability of precipitation (%)")
    pty: int | None = Field(default=None, description="Precipitation type code")
    pty_label: str | None = None
    sky: int | None = Field(default=None, description="Sky condition code")
    sky_label: str | None = None
    pcp: str | None = Field(default=None, description="1-hour precipitation")
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

# Request that takes symptoms as text input
class TriageRequest(BaseModel):
    

    message: str = Field(min_length=1, max_length=2000, description="Symptom description")
    generate_audio: bool = Field(default=True, description="Whether to generate guidance audio (WAV)")

# Symptom classification result
class TriageResponse(BaseModel):
    recognized_text: str | None = Field(default=None, description="Text recognized by STT (voice input only)")
    triage: dict[str, Any] = Field(description="Agent classification result")
    audio_url: str | None = Field(default=None, description="Path to the guidance audio. null if synthesis failed")