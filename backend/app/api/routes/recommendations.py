"""근거를 포함한 응급실 추천 API.

발표자료 슬라이드 21의 6단계를 그대로 순서대로 실행한다.

    1. 입력 구조화   증상 문장 → 요구자원 프로파일 + 채점 모드
    2. 1차 후보 선정 직선거리 5 → 10 → 20km (여기서는 경로 API를 부르지 않는다)
    3. 적합성 필터   필수 장비가 없는 기관을 제외
    4. TMAP 경로     필터를 통과한 후보에만 실제 거리·ETA·교통을 붙인다
    5. 점수 계산     일반/중증 모드 가중치를 전환해 적용
    6. Top 3 · Best  A등급 우선, A등급이 없으면 Best 를 표시하지 않는다

3단계까지 직선거리로 끝내는 이유는 TMAP 호출 비용 때문이다. 반경을 넓히는 동안
경로를 부르면 후보 수만큼 호출이 곱해진다.

기존 라우터와 같은 규칙을 지킨다.

    - 라우터가 자기 prefix("/recommendations")만 갖고, "/api/v1"은 main.py가 붙인다
    - 조회는 services 계층(hospitals/tmap/weather)에만 맡기고 SQL을 직접 쓰지 않는다
    - 공공 API 직접 호출은 Tmap 하나뿐 (출발지가 요청마다 달라 미리 적재할 수 없다)
"""

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
    """프로파일의 필수 자원을 실제로 보유한 기관을 후보에 보충한다.

    거리순 컷(prefilter_candidates)은 '가까운 곳 위주'라는 합리적 기본값이지만,
    화상·외상처럼 전문 자원이 특정 병원에만 있는 경우 그 병원이 컷 밖으로 밀린다.
    반경 안에 있고 필수 자원이 확인되는 기관만 최대 cap 곳까지 더한다.
    """
    required = [
        r.column
        for r in getattr(profile, "requirements", ())
        if getattr(r, "necessity", None) is Necessity.REQUIRED
    ]
    # hvec(응급실 일반병상)처럼 사실상 모든 기관이 가진 항목은 보충 대상이 아니다.
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
    """모드별 가중치에 앱 설정의 임계치를 얹는다.

    엔진 기본값은 설계서 §5.3.1의 예시(60분)이고 이 앱의 기본값은 90분이다.
    맞춰 두지 않으면 같은 병원이 목록 API에서는 `is_stale=false` 인데 추천
    리포트에서는 "갱신 지연"으로 떠서 화면끼리 어긋난다.
    """
    settings = get_settings()
    return ScoringConfig.for_mode(mode, stale_after_minutes=settings.stale_after_minutes)


def _structure(
    symptom: str | None, profile: str | None, severity: str, *, use_agent: bool = False
) -> StructuredInput:
    """1단계 - 증상 문장(또는 프론트가 이미 고른 프로파일)을 조건으로 바꾼다.

    profile 을 명시하면 그 값을 그대로 쓴다. 프론트가 증상 버튼으로 이미 고른
    경우가 그렇다. 문장만 있으면 키워드 규칙으로 해석한다.

    에이전트 호출은 기본으로 끈다(use_agent=False). Foundry 에이전트는 실측 20~22초라
    검색 응답에 그대로 얹히면 프론트 타임아웃(12초)과 개발 서버 프록시(30초)에 걸린다.
    키워드 규칙은 같은 프로파일 목록을 쓰고 즉시 끝난다. 에이전트 판정이 필요하면
    POST /recommendations/intake 로 따로 부르고, 그 결과의 profile 을 넘기면 된다.
    """
    agent_result: dict[str, Any] | None = None
    if use_agent and symptom and not profile and triage_service.is_configured():
        try:
            agent_result = triage_service.call_triage_agent(symptom)
        except triage_service.TriageAgentError as exc:
            # 분류 실패로 추천 자체를 막지 않는다. 키워드 규칙으로 내려간다.
            logger.warning("증상 분류 에이전트 실패, 키워드 규칙으로 진행: %s", exc)

    structured = structure_input(symptom or "", agent_result=agent_result, severity=severity)
    if not profile:
        return structured

    if profile not in PROFILES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"알 수 없는 요구자원 프로파일: {profile} (사용 가능: {sorted(PROFILES)})",
        )
    # 프로파일이 지정되면 모드는 그 프로파일 기준으로 다시 판정한다
    # (증상 문장이 없어도 화상·외상 등은 중증 모드로 잡혀야 한다).
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
    """4단계 - 적합성 필터를 통과한 후보에만 TMAP 경로를 붙인다.

    제외된 기관에 경로를 부르면 그만큼 호출이 낭비된다. 통과한 후보를 직선거리
    순으로 정렬해 앞에서부터 limit 곳만 조회한다.
    """
    if not tmap_service.is_configured():
        return {}

    excluded = {c.hpid for c in result.excluded}
    passed = [r for r in rows if str(r.get("hpid")) not in excluded]
    passed.sort(key=lambda r: r.get("distance_km") if r.get("distance_km") is not None else 1e9)

    # 최종 후보로 뽑힌 곳은 반드시 포함한다. 전문병원이 거리순 뒤에 있으면
    # 상위 limit 컷에서 빠져 "지도에는 경로가 있는데 점수는 0" 이 된다.
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
        # 한도 초과는 재시도해도 소용이 없다. 추적 스택 없이 원인만 또렷하게 남긴다.
        logger.warning("%s 직선거리 기준으로 진행합니다.", exc)
        return {}
    except tmap_service.TmapError:
        # 설계서 §5.7 - 경로 실패해도 응답은 살리고 직선거리로 임시 정렬한다.
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
    """반경 내 응급실을 평가해 추천 후보와 그 근거를 함께 반환한다.

    반환 payload 는 화면 한 장을 그대로 그릴 수 있는 형태다.
    pipeline / mode / tiles / score_breakdown / reasons / summary / warnings.
    """
    # (1) 입력 구조화
    structured = _structure(symptom, profile, severity, use_agent=use_agent)
    resource_profile = structured.profile
    config = _scoring_config(structured.mode)

    now = datetime.now(KST)
    current_weather = weather_service.get_current_by_latlon(lat, lon)

    # (2) 1차 후보 - 직선거리 기준. 경로 API는 아직 부르지 않는다.
    #     only_available / include_stale 을 여기서 걸면 안 된다. 0(가용 없음)과
    #     null(정보 없음)을 SQL이 먼저 뭉개 버려 설계서 F-05를 위반하게 된다.
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
        # 거리순 상위 N개만 보면 요구 자원을 실제로 갖춘 병원이 빠질 수 있다.
        # 예: 화상 전문(한림대한강성심)은 송파 기준 거리순 38위라 20개 컷에 안 든다.
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

    # (2~3) 반경을 넓히며 필터까지 통과시킨다. A등급이 3곳 모이면 멈춘다.
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

    # (4) 경로 - 필터를 통과한 후보에만 Tmap 호출
    routes = _attach_routes(rows, result, lat=lat, lon=lon, limit=config.route_candidates)
    if routes:
        # 카드 페이로드(build_card)가 지도용 경로를 함께 내려주려면 행에도 붙여야 한다.
        for row in rows:
            route = routes.get(str(row.get("hpid")))
            if route:
                row["route"] = route
        # (5) 실제 ETA가 들어왔으니 상대 점수를 전부 다시 매긴다 (외부 호출 없음).
        #     반경을 넓힌 이력은 새 결과에 없으므로 옮겨 준다 (화면이 그대로 보여준다).
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

    # (6) 감사로그는 응답이 아니라 로그로만 남긴다 (설계서 SEC-006 / §8.2).
    #     정밀좌표·원문 증상은 들어가지 않고 격자와 점수 구성만 남는다.
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
    # 하늘상태(맑음/구름많음/흐림)는 실황에 없고 예보에만 있다. 채점에는 쓰지 않지만
    # 이게 없으면 화면이 비만 아니면 전부 '맑음'으로 보인다.
    payload["weather"]["forecast"] = weather_service.list_forecast_by_latlon(lat, lon, hours=1)
    return payload


class IntakeRequest(BaseModel):
    """1단계 입력 구조화 요청."""

    message: str = Field(default="", description="증상 문장 (음성 전사 포함)")
    severity: str = Field(
        default="auto", description="auto | normal | severe. auto 면 문장으로 판정한다."
    )
    use_agent: bool = Field(
        default=True, description="Foundry 증상 분류 에이전트를 쓸지. 실패하면 키워드 규칙으로 내려간다."
    )


@router.post("/intake", summary="증상 문장 → 검색 조건 (추천 1단계)")
def create_intake(req: IntakeRequest) -> dict[str, Any]:
    """증상 문장을 요구자원 프로파일과 채점 모드로 바꿔 돌려준다.

    화면이 "무엇을 조건으로 잡았는지"를 먼저 보여 주고 사용자가 고칠 수 있게
    하려면 검색과 분리돼 있어야 한다. 결과의 profile/severity 를
    GET /recommendations 에 그대로 넘기면 된다.
    """
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
    """프론트의 증상 선택 버튼을 이 목록으로 그리면 서버와 어긋나지 않는다."""
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
