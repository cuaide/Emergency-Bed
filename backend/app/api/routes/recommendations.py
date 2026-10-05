from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.recommendation_engine import (
    MODE_LABELS,
    PROFILES,
    Necessity,
    RecommendationResult,
    ScoringConfig,
    ScoringMode,
    StructuredInput,
    build_audit_log,
    get_profile,
    recommend,
    recommend_with_radius_expansion,
    structure_input,
    to_api_payload,
)
from app.services import hospitals as hospital_service
from app.services import tmap as tmap_service
from app.services import triage as triage_service
from app.services import weather as weather_service

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


def _include_resource_holders(
    rows: list[dict[str, Any]],
    *,
    profile: Any,
    latitude: float,
    longitude: float,
    radius_km: float,
    cap: int = 8,
) -> list[dict[str, Any]]:
    required = [
        r.column
        for r in getattr(profile, "requirements", ())
        if getattr(r, "necessity", None) is Necessity.REQUIRED
    ]
    targets = [c for c in required if c not in {"hvec", "hvs01"}]
    if not targets:
        return rows

    have = {str(r.get("hpid")) for r in rows}
    extra = hospital_service.find_with_resources(
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        columns=targets,
        limit=cap,
    )
    added = [r for r in extra if str(r.get("hpid")) not in have]
    if added:
        logger.info("요구 자원 보유 기관 %d곳을 후보에 보충했다 (%s)", len(added), ", ".join(targets))
    return rows + added


def _scoring_config(mode: ScoringMode) -> ScoringConfig:
    settings = get_settings()
    return ScoringConfig.for_mode(mode, stale_after_minutes=settings.stale_after_minutes)


def _structure(
    symptom: str | None, profile: str | None, severity: str, *, use_agent: bool = False
) -> StructuredInput:
    agent_result: dict[str, Any] | None = None
    if use_agent and symptom and not profile and triage_service.is_configured():
        try:
            agent_result = triage_service.call_triage_agent(symptom)
        except triage_service.TriageAgentError as exc:
            logger.warning("증상 분류 에이전트 실패, 키워드 규칙으로 진행: %s", exc)

    structured = structure_input(symptom or "", agent_result=agent_result, severity=severity)
    if not profile:
        return structured

    if profile not in PROFILES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"알 수 없는 요구자원 프로파일: {profile} (사용 가능: {sorted(PROFILES)})",
        )
    return structure_input(
        symptom or get_profile(profile).label,
        agent_result={"profile": profile},
        severity=severity,
    )


def _attach_routes(
    rows: list[dict[str, Any]],
    result: RecommendationResult,
    *,
    lat: float,
    lon: float,
    limit: int,
) -> dict[str, dict[str, Any]]:
    if not tmap_service.is_configured():
        return {}

    excluded = {c.hpid for c in result.excluded}
    passed = [r for r in rows if str(r.get("hpid")) not in excluded]
    passed.sort(key=lambda r: r.get("distance_km") if r.get("distance_km") is not None else 1e9)
    ranked = {c.hpid for c in result.candidates}
    targets = [r for r in passed if str(r.get("hpid")) in ranked]
    have = {str(r.get("hpid")) for r in targets}
    for row in passed:
        if len(targets) >= limit:
            break
        if str(row.get("hpid")) not in have:
            targets.append(row)
            have.add(str(row.get("hpid")))

    try:
        enriched = tmap_service.attach_routes(
            targets, start_latitude=lat, start_longitude=lon, limit=len(targets)
        )
    except tmap_service.TmapQuotaError as exc:

        logger.warning("%s 직선거리 기준으로 진행합니다.", exc)
        return {}
    except tmap_service.TmapError:
        logger.warning("Tmap 경로 조회 실패, 직선거리 기준으로 진행", exc_info=True)
        return {}

    return {str(r["hpid"]): r["route"] for r in enriched if r.get("route")}


@router.get("", summary="근거 포함 응급실 추천 (최대 3곳)")
def get_recommendations(
    lat: float = Query(description="현재 위치 위도", ge=-90, le=90),
    lon: float = Query(description="현재 위치 경도", ge=-180, le=180),
    profile: str | None = Query(
        default=None,
        description=f"요구자원 프로파일. 비우면 symptom 으로 판정한다. 사용 가능: {sorted(PROFILES)}",
    ),
    symptom: str | None = Query(
        default=None, description="증상 문장. profile 이 없을 때 이 문장으로 조건을 만든다."
    ),
    severity: str = Query(
        default="auto",
        description=(
            "채점 모드. auto(증상·프로파일로 자동 판정) / normal(이동 시간 중심) / "
            "severe(치료 역량 중심). 의료진의 KTAS 확정 분류가 아니라 표시 우선순위 정책이다."
        ),
    ),
    radius_km: float | None = Query(
        default=None,
        gt=0,
        le=100,
        description="반경을 고정한다. 비우면 5 → 10 → 20km 로 단계적으로 넓힌다.",
    ),
    include_detail: bool = Query(
        default=False,
        description="점수 수식·근거 상세 포함 여부. 목록 화면은 false, 추천 리포트 화면은 true",
    ),
    use_agent: bool = Query(
        default=False,
        description=(
            "symptom 해석에 Foundry 에이전트를 쓸지. 실측 20초 이상 걸려 기본은 끔이고, "
            "끄면 같은 프로파일 목록을 쓰는 키워드 규칙으로 즉시 판정한다."
        ),
    ),
    session_id: str = Query(default="anonymous", description="비식별 세션 ID (감사로그용)"),
) -> dict[str, Any]:
    structured = _structure(symptom, profile, severity, use_agent=use_agent)
    resource_profile = structured.profile
    config = _scoring_config(structured.mode)

    now = datetime.now(KST)
    current_weather = weather_service.get_current_by_latlon(lat, lon)
    fetched: dict[float, list[dict[str, Any]]] = {}

    def fetch_rows(radius: float) -> list[dict[str, Any]]:
        if radius in fetched:
            return fetched[radius]
        rows = hospital_service.find_nearby(
            latitude=lat,
            longitude=lon,
            radius_km=radius,
            only_available=False,
            include_stale=True,
            limit=config.prefilter_candidates,
        )
        rows = _include_resource_holders(
            rows,
            profile=resource_profile,
            latitude=lat,
            longitude=lon,
            radius_km=radius,
        )
        fetched[radius] = rows
        return rows

    scoring_kwargs: dict[str, Any] = {"now": now, "weather": current_weather}

    if radius_km is not None:
        rows = fetch_rows(radius_km)
        result = recommend(
            rows, profile=resource_profile, config=config, radius_km=radius_km, **scoring_kwargs
        )
    else:
        result = recommend_with_radius_expansion(
            fetch_rows, profile=resource_profile, config=config, **scoring_kwargs
        )
        rows = fetched.get(result.radius_km or 0, [])

    if not rows:
        return {
            "generated_at": now.isoformat(),
            "recommendations": [],
            "notes": [
                f"반경 {(result.radius_km or 0):.0f}km 안에 후보가 없습니다. "
                "범위를 넓히거나 119·응급의료정보센터(1339)로 확인해 주세요."
            ],
        }

    routes = _attach_routes(rows, result, lat=lat, lon=lon, limit=config.route_candidates)
    if routes:
        for row in rows:
            route = routes.get(str(row.get("hpid")))
            if route:
                row["route"] = route
        radius_steps = result.radius_steps
        result = recommend(
            rows,
            profile=resource_profile,
            config=config,
            radius_km=result.radius_km,
            routes=routes,
            **scoring_kwargs,
        )
        result.radius_steps = radius_steps

    logger.info(
        "recommendation_audit",
        extra={
            "audit": build_audit_log(
                result,
                session_id=session_id,
                user_latitude=lat,
                user_longitude=lon,
            )
        },
    )

    payload = to_api_payload(
        result,
        include_detail=include_detail,
        structured=structured,
        routed_count=len(routes) if routes else None,
    )
    payload["weather"]["forecast"] = weather_service.list_forecast_by_latlon(lat, lon, hours=1)
    return payload


class IntakeRequest(BaseModel):

    message: str = Field(default="", description="증상 문장 (음성 전사 포함)")
    severity: str = Field(
        default="auto", description="auto | normal | severe. auto 면 문장으로 판정한다."
    )
    use_agent: bool = Field(
        default=True, description="Foundry 증상 분류 에이전트를 쓸지. 실패하면 키워드 규칙으로 내려간다."
    )


@router.post("/intake", summary="증상 문장 → 검색 조건 (추천 1단계)")
def create_intake(req: IntakeRequest) -> dict[str, Any]:
    agent_result: dict[str, Any] | None = None
    if req.use_agent and req.message.strip() and triage_service.is_configured():
        try:
            agent_result = triage_service.call_triage_agent(req.message)
        except triage_service.TriageAgentError as exc:
            logger.warning("증상 분류 에이전트 실패, 키워드 규칙으로 진행: %s", exc)

    structured = structure_input(
        req.message, agent_result=agent_result, severity=req.severity
    )
    payload = structured.to_dict()
    payload["agent_used"] = agent_result is not None
    payload["modes"] = [
        {"key": mode.value, "label": MODE_LABELS[mode]} for mode in ScoringMode
    ]
    return payload


@router.get("/profiles", summary="선택 가능한 요구자원 프로파일 목록")
def list_profiles() -> list[dict[str, Any]]:
    return [
        {
            "key": profile.key,
            "label": profile.label,
            "required": [
                {"column": r.column, "label": r.display_label} for r in profile.required()
            ],
            "optional": [
                {"column": r.column, "label": r.display_label} for r in profile.optional()
            ],
            "needs_surgery": profile.needs_surgery,
            "severe_by_default": profile.severe_by_default,
            "caution": profile.caution,
            "unsupported_equipment": list(profile.unsupported_equipment),
        }
        for profile in PROFILES.values()
    ]
