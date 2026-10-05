from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

class ResourceKind(StrEnum):
    COUNT = "count"  
    FLAG = "flag"  
    TEXT = "text"  

class DataState(StrEnum):
    AVAILABLE = "available"
    NONE = "none"
    QUEUED = "queued"
    UNKNOWN = "unknown"
    STALE = "stale"


class ScoringMode(StrEnum):
    """Weight-switching mode (slide 22).

    NORMAL is distance-centric (travel time 50%); SEVERE is treatment-capability-centric
    (resources 40% + center capability 20%, travel time 30%).

    "Severe" here is not a confirmed KTAS classification made by medical staff; it is
    this service's safety policy for changing the priority of search results. The
    on-screen wording must say the same.
    """

    NORMAL = "normal"
    SEVERE = "severe"

class Freshness(StrEnum):
    FRESH = "fresh"  
    RECENT = "recent" 
    AGING = "aging"  
    STALE = "stale"  

class Necessity(StrEnum):

    REQUIRED = "required"
    OPTIONAL = "optional"

@dataclass(frozen=True)
class ResourceSpec:
    
    column: str
    label: str
    kind: ResourceKind
    canonical: str
    supported: bool = True
    note: str = ""


def _spec(column: str, label: str, kind: ResourceKind, canonical: str, **kw: Any) -> ResourceSpec:
    return ResourceSpec(column=column, label=label, kind=kind, canonical=canonical, **kw)


# hv1 ~ hv12: detailed items of the Public Data Portal real-time ER available beds
HV_RESOURCES: tuple[ResourceSpec, ...] = (
    _spec("hv2", "내과중환자실", ResourceKind.COUNT, "available_internal_medicine_icu_beds"),
    _spec("hv3", "외과중환자실", ResourceKind.COUNT, "available_surgical_icu_beds"),
    _spec("hv4", "외과입원실(정형외과)", ResourceKind.COUNT, "available_orthopedic_inpatient_beds"),
    _spec("hv5", "신경과입원실", ResourceKind.COUNT, "available_neurology_inpatient_beds"),
    _spec("hv6", "신경외과중환자실", ResourceKind.COUNT, "available_neurosurgery_icu_beds"),
    _spec("hv7", "약물중환자", ResourceKind.COUNT, "available_drug_poisoning_icu_beds"),
    _spec("hv8", "화상중환자", ResourceKind.COUNT, "available_burn_icu_beds"),
    _spec("hv9", "외상중환자", ResourceKind.COUNT, "available_trauma_icu_beds"),
    _spec(
        "hv10",
        "VENTI(소아)",
        ResourceKind.FLAG,
        "pediatric_ventilator_available",
        note="원본이 Y/N 문자열이다. INTEGER 컬럼으로 캐스팅하면 값이 사라지므로 raw JSONB를 함께 본다.",
    ),
    _spec(
        "hv11",
        "인큐베이터(보육기)",
        ResourceKind.FLAG,
        "incubator_available",
        note="원본이 Y/N 문자열이다. raw JSONB 폴백 필요.",
    ),
    _spec("hv12", "소아당직의", ResourceKind.FLAG, "pediatric_on_duty_doctor"),
)

# Aggregate beds / reference beds
AGGREGATE_RESOURCES: tuple[ResourceSpec, ...] = (
    _spec("hvec", "응급실 일반병상", ResourceKind.COUNT, "available_er_beds"),
    _spec("hvoc", "수술실", ResourceKind.COUNT, "available_operating_rooms"),
    _spec("hvcc", "신경외과 중환자실(집계)", ResourceKind.COUNT, "available_neurosurgery_icu_beds"),
    _spec("hvncc", "신생아 중환자실", ResourceKind.COUNT, "available_neonatal_icu_beds"),
    _spec("hvccc", "흉부외과 중환자실", ResourceKind.COUNT, "available_thoracic_icu_beds"),
    _spec("hvicc", "일반 중환자실(집계)", ResourceKind.COUNT, "available_general_icu_beds"),
    _spec("hvgc", "입원실", ResourceKind.COUNT, "available_inpatient_beds"),
    _spec("hvs01", "응급실 기준병상", ResourceKind.COUNT, "standard_er_beds"),
)

# Equipment (design doc §2.4 - of the 7 types the prompt asks for, only those that actually exist are supported=True)
EQUIPMENT_RESOURCES: tuple[ResourceSpec, ...] = (
    _spec("hvctayn", "CT", ResourceKind.FLAG, "CT_available"),
    _spec("hvmriayn", "MRI", ResourceKind.FLAG, "MRI_available"),
    _spec("hvangioayn", "혈관조영촬영기", ResourceKind.FLAG, "angiography_available"),
    _spec("hvventiayn", "인공호흡기(성인)", ResourceKind.FLAG, "ventilator_available"),
    _spec("hvamyn", "구급차", ResourceKind.FLAG, "ambulance_available"),
    _spec(
        "hyperbaric_oxygen_available",
        "고압산소치료기",
        ResourceKind.FLAG,
        "hyperbaric_oxygen_available",
        supported=False,
        note="설계서 §2.4 - 현재 원천 데이터에 없다. 필터로 쓰면 안 되고 '확인 필요'로만 표시한다.",
    ),
)

ALL_RESOURCES: tuple[ResourceSpec, ...] = HV_RESOURCES + AGGREGATE_RESOURCES + EQUIPMENT_RESOURCES

RESOURCE_BY_COLUMN: dict[str, ResourceSpec] = {spec.column: spec for spec in ALL_RESOURCES}
RESOURCE_BY_CANONICAL: dict[str, ResourceSpec] = {spec.canonical: spec for spec in ALL_RESOURCES}

# Whitelist for mapping column names returned by the LLM to actual columns (design doc PROMPT-001).
# The validator must reject any name not in this list.
ALLOWED_RESOURCE_NAMES: frozenset[str] = frozenset(
    list(RESOURCE_BY_COLUMN) + list(RESOURCE_BY_CANONICAL)
)


def resolve_spec(name: str) -> ResourceSpec | None:
    """Look up a spec by column name (hv8) or canonical name (available_burn_icu_beds)."""
    return RESOURCE_BY_COLUMN.get(name) or RESOURCE_BY_CANONICAL.get(name)


# ---------------------------------------------------------------------------
# Emergency medical institution tiers (design doc F-02, §5.4 "prefer high-tier emergency centers")
# ---------------------------------------------------------------------------

CENTER_TIERS: tuple[tuple[str, int], ...] = (
    ("권역응급의료센터", 4),
    ("전문응급의료센터", 4),
    ("지역응급의료센터", 3),
    ("지역응급의료기관", 2),
    ("응급실운영신고기관", 1),
)


def center_tier(duty_emcls_name: str | None) -> int:
    """Map the emergency institution classification to a 0-4 tier. 0 means unknown classification (subject to F-02)."""
    if not duty_emcls_name:
        return 0
    text = str(duty_emcls_name).strip()
    for keyword, rank in CENTER_TIERS:
        if keyword in text:
            return rank
    return 0


# ---------------------------------------------------------------------------
# Required-resource profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Requirement:
    """One resource required by the patient's condition.

    weight is the points column of the user table (default 20). The headline clinical
    fit score uses the 35/20/0 bands from design doc §5.3.1 as-is; weight is used to
    compute "what percentage of required resources were actually confirmed", which
    feeds the reasoning text and tie-breaking.
    """

    column: str
    necessity: Necessity = Necessity.REQUIRED
    weight: int = 20
    label: str | None = None

    @property
    def spec(self) -> ResourceSpec | None:
        return resolve_spec(self.column)

    @property
    def display_label(self) -> str:
        if self.label:
            return self.label
        spec = self.spec
        return spec.label if spec else self.column

    @property
    def is_required(self) -> bool:
        return self.necessity is Necessity.REQUIRED


@dataclass(frozen=True)
class ResourceProfile:
    """Bundle of required resources for one symptom category.

    A direct port of the bed mapping policy in design doc §5.4.
    """

    key: str
    label: str
    requirements: tuple[Requirement, ...]
    needs_surgery: bool = False
    prefer_high_tier_center: bool = False
    caution: str = ""
    unsupported_equipment: tuple[str, ...] = field(default_factory=tuple)
    # With this profile, score in severe mode even without a separate signal in the symptom text.
    # (This is a service policy that changes search priority, not a confirmed clinical classification.)
    severe_by_default: bool = False

    def required(self) -> tuple[Requirement, ...]:
        return tuple(r for r in self.requirements if r.is_required)

    def optional(self) -> tuple[Requirement, ...]:
        return tuple(r for r in self.requirements if not r.is_required)

    def bed_requirements(self) -> tuple[Requirement, ...]:
        """Select only the resources used for the "related beds 15 points" of the availability proxy.

        ER (hvec) and operating room (hvoc) are scored separately at 5 points each in
        §5.3.1, so they must be excluded here to avoid double counting. Equipment and
        reference beds are also excluded.
        """
        out = []
        for req in self.requirements:
            spec = req.spec
            if spec is None or not spec.supported:
                continue
            if spec.column in {"hvs01", "hvamyn", "hvec", "hvoc"}:
                continue
            if spec in EQUIPMENT_RESOURCES:
                continue
            out.append(req)
        return tuple(out)


def _req(column: str, necessity: Necessity = Necessity.REQUIRED, weight: int = 20) -> Requirement:
    return Requirement(column=column, necessity=necessity, weight=weight)


# Abbreviations to keep the profile table short
_R = Necessity.REQUIRED
_O = Necessity.OPTIONAL

# The design doc §5.4 table ported directly into profiles.
PROFILES: dict[str, ResourceProfile] = {
    "cardiac": ResourceProfile(
        key="cardiac",
        label="심한 흉통·심혈관 중증 의심",
        requirements=(
            _req("hvccc", _R),
            _req("hv2", _O),
            _req("hvangioayn", _O),
        ),
        prefer_high_tier_center=True,
        severe_by_default=True,
        caution="혈관조영 등 장비 정보가 추가로 필요하다.",
    ),
    "neuro": ResourceProfile(
        key="neuro",
        label="마비·의식저하·뇌신경계 중증",
        requirements=(
            _req("hv6", _R),
            _req("hv5", _R),
            _req("hvctayn", _O),
            _req("hvmriayn", _O),
        ),
        prefer_high_tier_center=True,
        severe_by_default=True,
        caution="CT/MRI·뇌혈관 치료가능 정보가 필요하다.",
    ),
    "trauma": ResourceProfile(
        key="trauma",
        label="중증 외상·절단·대량출혈",
        requirements=(_req("hv9", _R), _req("hvoc", _R)),
        needs_surgery=True,
        prefer_high_tier_center=True,
        severe_by_default=True,
        caution="수술실과 외상 대응 여부를 함께 요구한다.",
    ),
    "burn": ResourceProfile(
        key="burn",
        label="중증 화상",
        requirements=(_req("hv8", _R), _req("hvec", _O)),
        prefer_high_tier_center=True,
        severe_by_default=True,
        caution="특수 화상 치료 가능 여부는 별도 확인이 필요하다.",
    ),
    "poisoning": ResourceProfile(
        key="poisoning",
        label="심각한 약물·독극물 중독",
        requirements=(_req("hv7", _R),),
        prefer_high_tier_center=True,
        severe_by_default=True,
        caution="중독센터·고압산소 등 추가 정보가 필요하다.",
        unsupported_equipment=("hyperbaric_oxygen_available",),
    ),
    "abdominal_surgery": ResourceProfile(
        key="abdominal_surgery",
        label="복부·외과 수술 가능성",
        requirements=(_req("hv3", _R), _req("hvoc", _R)),
        needs_surgery=True,
        caution="수술 필요는 확진이 아니라 자원 탐색용 추정이다.",
    ),
    "pediatric_respiratory": ResourceProfile(
        key="pediatric_respiratory",
        label="소아 호흡부전",
        requirements=(_req("hv10", _R), _req("hvec", _O)),
        prefer_high_tier_center=True,
        severe_by_default=True,
    ),
    "neonatal": ResourceProfile(
        key="neonatal",
        label="신생아·산모 관련",
        requirements=(_req("hv11", _R), _req("hvncc", _O)),
        prefer_high_tier_center=True,
        severe_by_default=True,
    ),
    "unspecified_icu": ResourceProfile(
        key="unspecified_icu",
        label="특정 중환자실 불명확",
        # Use the actual general ICU column (hvicc) instead of hv1.
        requirements=(_req("hvicc", _R), _req("hvec", _R)),
        prefer_high_tier_center=True,
        severe_by_default=True,
        caution="불확실 상태를 유지하고 고등급 응급센터를 우선한다.",
    ),
    "general": ResourceProfile(
        key="general",
        label="경증 / 자원 특정 불필요",
        requirements=(_req("hvec", _R),),
    ),
}


def get_profile(key: str) -> ResourceProfile:
    if key not in PROFILES:
        raise KeyError(f"알 수 없는 요구자원 프로파일: {key} (사용 가능: {sorted(PROFILES)})")
    return PROFILES[key]


# ---------------------------------------------------------------------------
# Stage 1: Input structuring (slide 21)
# ---------------------------------------------------------------------------

# Symptom text → required-resource profile. Deterministic fallback rules used when no
# agent is attached or its response can't be trusted. If several match, the one with
# more hits wins; on a tie, the earlier one in this order wins. That's why the most
# time-critical categories are at the top.
SYMPTOM_RULES: tuple[tuple[str, str], ...] = (
    ("흉통|가슴.?통|심장|심근|가슴이 조|가슴을 쥐", "cardiac"),
    ("마비|의식|두통|뇌졸중|어눌|한쪽.?힘|쓰러", "neuro"),
    ("외상|출혈|절단|골절|교통사고|찔림|추락", "trauma"),
    ("화상|데였|끓는|불에", "burn"),
    ("중독|약물|독극물|농약|가스 흡입", "poisoning"),
    ("소아 호흡|영아 호흡|아기.?숨", "pediatric_respiratory"),
    ("신생아|분만|산모|조산", "neonatal"),
    ("호흡|숨쉬기|숨을 못|산소|질식", "unspecified_icu"),
    ("복통|복부|배가 아|맹장|장폐색", "abdominal_surgery"),
)

# Signals that switch to severe mode. Detected directly from the text, independent of the profile.
SEVERITY_SIGNALS: tuple[str, ...] = (
    "의식 없",
    "의식이 없",
    "반응이 없",
    "숨을 못",
    "호흡 곤란",
    "숨쉬기 어려",
    "대량 출혈",
    "대량출혈",
    "피가 멈추지",
    "경련",
    "쓰러",
    "심정지",
    "심장이 멈",
    "청색증",
    "마비",
)


@dataclass(frozen=True)
class StructuredInput:
    """Result of turning symptom text into conditions used for scoring (slide 21, stage 1)."""

    profile_key: str
    mode: ScoringMode
    matched_keywords: tuple[str, ...] = ()
    severity_signals: tuple[str, ...] = ()
    source: str = "rules"  # rules | agent
    note: str = ""

    @property
    def profile(self) -> ResourceProfile:
        return get_profile(self.profile_key)

    def to_dict(self) -> dict[str, Any]:
        profile = self.profile
        return {
            "profile": profile.key,
            "profile_label": profile.label,
            "mode": self.mode.value,
            "mode_label": MODE_LABELS[self.mode],
            "severe": self.mode is ScoringMode.SEVERE,
            "matched_keywords": list(self.matched_keywords),
            "severity_signals": list(self.severity_signals),
            "source": self.source,
            "note": self.note,
            "required": [
                {"column": r.column, "label": r.display_label} for r in profile.required()
            ],
        }


def structure_input(
    text: str,
    *,
    agent_result: dict[str, Any] | None = None,
    severity: str = "auto",
) -> StructuredInput:
    """Turn symptom text (+ the agent classification result, if any) into a profile and scoring mode.

    If the profile returned by the agent is on our whitelist, use it; otherwise fall
    back to keyword rules. Blindly trusting an unknown value would mark the wrong
    resources as required and wipe out the candidate list entirely.

    severity is "auto" | "normal" | "severe". With auto, the mode is decided by the
    severity signals in the text and the profile's default.
    """
    body = (text or "").strip()
    source = "rules"
    profile_key = ""
    note = ""

    if agent_result:
        candidate = str(
            agent_result.get("profile")
            or agent_result.get("profile_key")
            or agent_result.get("category")
            or ""
        ).strip()
        if candidate in PROFILES:
            profile_key, source = candidate, "agent"
        elif candidate:
            note = f"에이전트가 반환한 프로파일 '{candidate}' 을 알 수 없어 키워드 규칙으로 대체했다."

    # Pick the category with the most keyword hits. Previously it was "a later match overwrites",
    # which classified "가슴이 조이고 의식이 없어요" as neuro and missed the chest-pain resources.
    hits: list[tuple[int, int, str]] = []
    for order, (pattern, key) in enumerate(SYMPTOM_RULES):
        count = len(re.findall(pattern, body))
        if count:
            hits.append((-count, order, key))
    hits.sort()
    matched = [key for _, _, key in hits]
    if source != "agent" and matched:
        profile_key = matched[0]
    if not profile_key:
        profile_key = "general"

    signals = tuple(s for s in SEVERITY_SIGNALS if s in body)
    if agent_result:
        # If the agent returns red flags separately, treat them as severity signals too.
        flags = agent_result.get("red_flags") or agent_result.get("redFlags") or []
        if isinstance(flags, list):
            signals = signals + tuple(str(f) for f in flags if f)

    requested = (severity or "auto").strip().lower()
    if requested in {"severe", "critical", "high"}:
        mode = ScoringMode.SEVERE
    elif requested in {"normal", "low", "mild"}:
        mode = ScoringMode.NORMAL
    else:
        mode = (
            ScoringMode.SEVERE
            if signals or get_profile(profile_key).severe_by_default
            else ScoringMode.NORMAL
        )

    return StructuredInput(
        profile_key=profile_key,
        mode=mode,
        matched_keywords=tuple(matched),
        severity_signals=tuple(dict.fromkeys(signals)),
        source=source,
        note=note,
    )


# ---------------------------------------------------------------------------
# Tabular input → profile
# ---------------------------------------------------------------------------


def profile_from_table(
    text: str,
    *,
    key: str = "custom",
    label: str = "사용자 지정 요구자원",
    needs_surgery: bool = False,
) -> ResourceProfile:
    """Build a profile from a tab-separated table.

    Expected format (column order):
        resource name <TAB> column <TAB> points <TAB> 필수|옵션 (required|optional) <TAB> current value <TAB> display label

        내과중환자실	hv2	20	필수	3	내과중환자실
        VENTI(소아)	hv10	20	필수	Y	VENTI(소아)

    The 5th column (current value) is ignored for the profile. Values must be read
    from hospital data; they must not be mixed into the requirement definition.
    If you need those values for a demo, use :func:`sample_row_from_table`.
    """
    requirements: list[Requirement] = []
    unsupported: list[str] = []

    for line in text.strip().splitlines():
        parts = [cell.strip() for cell in line.split("\t")]
        if len(parts) < 2 or not parts[1]:
            continue
        column = parts[1]
        weight = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 20
        necessity = _R if (len(parts) > 3 and parts[3].startswith("필수")) else _O
        display = parts[5] if len(parts) > 5 and parts[5] else parts[0]

        spec = resolve_spec(column)
        if spec is not None and not spec.supported:
            unsupported.append(column)
            continue

        requirements.append(
            Requirement(column=column, necessity=necessity, weight=weight, label=display or None)
        )

    return ResourceProfile(
        key=key,
        label=label,
        requirements=tuple(requirements),
        needs_surgery=needs_surgery,
        unsupported_equipment=tuple(unsupported),
    )


def sample_row_from_table(text: str) -> dict[str, Any]:
    """Extract only the 5th column (current value) of the table into a hospital-like row. Demo/test only."""
    row: dict[str, Any] = {}
    for line in text.strip().splitlines():
        parts = [cell.strip() for cell in line.split("\t")]
        if len(parts) < 5 or not parts[1]:
            continue
        raw = parts[4]
        row[parts[1]] = None if raw in {"", "-", "null"} else raw
    return row


# ===========================================================================
# 2. Evidence data structures - state classification, Evidence, ScoreComponent, FilterOutcome
# ===========================================================================

KST = timezone(timedelta(hours=9))

# Design doc §5.6 - never assert "treatment available / admission confirmed".
# Only assertions should be blocked; negations ("수용 확정이 아닙니다") must pass,
# so we also check whether a negation marker follows the expression.
FORBIDDEN_PATTERNS: tuple[str, ...] = (
    r"수용\s*확정",
    r"진료(가)?\s*가능(합니다|해요|함)",
    r"치료\s*(가능합니다|보장)",
    r"입원\s*확정",
    r"바로\s*(진료|입원)\s*(가능|됩니다)",
)

# Markers that, when they immediately follow the expressions above, make it a denial rather than an assertion.
_NEGATION_MARKERS: tuple[str, ...] = (
    "아닙니다",
    "아니다",
    "아니며",
    "아니므로",
    "이 아",
    "가 아",
    "을 의미하지 않",
    "를 의미하지 않",
    "하지 않습니다",
    "여부",
    "금지",
    "없습니다",
)

_NEGATION_WINDOW = 14  # Pass if a negation marker appears within this many characters after the expression


class CapacityClaimError(AssertionError):
    """Raised when text contains an "admission confirmed" assertion forbidden by design doc §5.6."""


def assert_no_capacity_claim(text: str) -> str:
    """Check that user-facing text does not assert admission/treatment.

    Sentences that deny or ask for confirmation, such as "수용 확정이 아닙니다" or
    "진료 가능 여부를 확인해 주세요", are valid and pass.
    """
    for pattern in FORBIDDEN_PATTERNS:
        for match in re.finditer(pattern, text):
            tail = text[match.end() : match.end() + _NEGATION_WINDOW]
            if any(marker in tail for marker in _NEGATION_MARKERS):
                continue
            raise CapacityClaimError(
                f"설계서 §5.6 위반 - 수용/진료를 단정하는 표현: {match.group(0)!r}"
                f" / 문장: {text!r}"
            )
    return text


# ---------------------------------------------------------------------------
# A single piece of evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Evidence:
    """Source and reason for one score/filter decision.

    code       : Evidence code (EV-BED-AVAILABLE, etc.). Logs don't break when UI wording changes.
    label      : One line shown to the user (e.g. "화상중환자 3자리 가용")
    detail     : Why it was judged that way (e.g. "hv8=3, 6분 전 갱신")
    column     : Source column
    canonical  : Name from design doc Appendix A
    raw_value  : Original value before processing
    state      : DATA-006 state
    source     : NMC / TMAP / KMA / RULE
    observed_at: Reference time of the value
    age_minutes: Minutes elapsed since the reference time
    """

    code: str
    label: str
    detail: str = ""
    column: str | None = None
    canonical: str | None = None
    raw_value: Any = None
    state: DataState | None = None
    source: str = "RULE"
    observed_at: datetime | None = None
    age_minutes: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value if self.state else None
        data["observed_at"] = self.observed_at.isoformat() if self.observed_at else None
        return data


# ---------------------------------------------------------------------------
# Resource value classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResourceReading:
    """Result of reading one resource from a hospital row and classifying its state."""

    spec: ResourceSpec
    raw_value: Any
    count: int | None
    state: DataState
    freshness: Freshness
    observed_at: datetime | None
    age_minutes: float | None
    source_field: str  # Where the value was actually read from (column or raw JSONB)
    # Absolute value when the original is negative. Interpreted as the number of people waiting, not a bed count.
    queue_depth: int | None = None

    @property
    def is_available(self) -> bool:
        return self.state is DataState.AVAILABLE

    @property
    def is_unknown(self) -> bool:
        return self.state in (DataState.UNKNOWN, DataState.STALE)

    @property
    def is_empty(self) -> bool:
        """State where "no availability" is confirmed. null, stale and queued (negative) are not included."""
        return self.state is DataState.NONE

    @property
    def is_queued(self) -> bool:
        """State where the original is negative. The value itself is confirmed and read as a waiting line."""
        return self.state is DataState.QUEUED

    def backlogged(self, threshold: int) -> bool:
        """Whether the queue is at or above the threshold (default 6 people) and should be deprioritized."""
        return self.is_queued and (self.queue_depth or 0) >= threshold

    def value_text(self) -> str:
        if self.state is DataState.UNKNOWN:
            return "정보 없음"
        if self.state is DataState.STALE:
            return "갱신 지연"
        if self.state is DataState.QUEUED:
            return f"대기 {self.queue_depth}명 추정"
        if self.spec.kind is ResourceKind.FLAG:
            return "가용" if self.state is DataState.AVAILABLE else "불가"
        # If "0개" goes into the reasoning text as-is, it reads like "화상중환자 0개",
        # which doesn't distinguish "no spare capacity" from "no such resource".
        if self.state is DataState.NONE:
            return "가용 0"
        return f"{self.count}개"

    def to_evidence(self, *, required: bool) -> Evidence:
        tag = "필수" if required else "옵션"
        if self.state is DataState.AVAILABLE:
            code, label = "EV-RES-OK", f"{self.spec.label} {self.value_text()}"
        elif self.state is DataState.NONE:
            code, label = "EV-RES-EMPTY", f"{self.spec.label} 가용 없음"
        elif self.state is DataState.QUEUED:
            code, label = "EV-RES-QUEUED", f"{self.spec.label} {self.value_text()}"
        elif self.state is DataState.STALE:
            code, label = "EV-RES-STALE", f"{self.spec.label} 갱신 지연"
        else:
            code, label = "EV-RES-UNKNOWN", f"{self.spec.label} 정보 없음"

        age = f"{self.age_minutes:.0f}분 전 갱신" if self.age_minutes is not None else "갱신시각 없음"
        return Evidence(
            code=code,
            label=f"[{tag}] {label}",
            detail=f"{self.source_field}={self.raw_value!r} · {age}",
            column=self.spec.column,
            canonical=self.spec.canonical,
            raw_value=self.raw_value,
            state=self.state,
            source="NMC",
            observed_at=self.observed_at,
            age_minutes=self.age_minutes,
        )


def classify_freshness(
    age_minutes: float | None,
    *,
    buckets: tuple[int, int, int] = (10, 30, 60),
) -> Freshness:
    """Freshness bands from design doc §5.3.1 (default 10/30/60 minutes)."""
    if age_minutes is None:
        return Freshness.STALE
    fresh, recent, aging = buckets
    if age_minutes <= fresh:
        return Freshness.FRESH
    if age_minutes <= recent:
        return Freshness.RECENT
    if age_minutes <= aging:
        return Freshness.AGING
    return Freshness.STALE


# Upper bound on the bed/operating room count an institution can report. Anything larger is treated as an input error.
MAX_PLAUSIBLE_COUNT = 1000


def _coerce_count(raw: Any, kind: ResourceKind) -> tuple[int | None, DataState]:
    """Raw value → (count, state). Never mixes up null and 0."""
    if raw is None:
        return None, DataState.UNKNOWN

    if isinstance(raw, bool):
        return (1, DataState.AVAILABLE) if raw else (0, DataState.NONE)

    text = str(raw).strip()
    if text == "" or text.lower() in {"null", "none", "-"}:
        return None, DataState.UNKNOWN

    upper = text.upper()
    if upper in {"Y", "TRUE"}:
        return 1, DataState.AVAILABLE
    if upper in {"N", "FALSE"}:
        return 0, DataState.NONE

    try:
        number = int(float(text))
    except ValueError:
        # Text resources such as the on-duty doctor's name count as available if a value is present.
        if kind is ResourceKind.TEXT:
            return 1, DataState.AVAILABLE
        return None, DataState.UNKNOWN

    if number < 0:
        # Slide 22 - collapsing negatives into "no information" would push even institutions
        # that can provide care down to Grade B. Inspecting the real data, negatives appear to
        # represent the number of people waiting. The value itself is confirmed, so keep it as
        # QUEUED and pass the absolute value along as the queue size.
        # score_availability decides the penalty size, split into -1~-5 / -6 or below.
        if -number > MAX_PLAUSIBLE_COUNT:
            return None, DataState.UNKNOWN
        return number, DataState.QUEUED
    if number > MAX_PLAUSIBLE_COUNT:
        # Trusting a value where the institution mistyped the digits (hvoc=12312 actually
        # observed at the National Medical Center) would emit "수술실 12312실 가용" as evidence
        # and max out the availability score. Even the largest hospitals in Korea don't exceed
        # three digits for operating rooms or beds, so drop it to "no information".
        return None, DataState.UNKNOWN
    if number == 0:
        return 0, DataState.NONE
    return number, DataState.AVAILABLE


def read_resource(
    row: dict[str, Any],
    spec: ResourceSpec,
    *,
    observed_at: datetime | None,
    now: datetime,
    stale_after_minutes: int = 60,
    freshness_buckets: tuple[int, int, int] = (10, 30, 60),
) -> ResourceReading:
    """Read one resource from a hospital row.

    If the column is empty, fall back to the raw JSONB. hv10 (pediatric VENTI) and
    hv11 (incubator) are Y/N strings in the source, so their values can be lost when
    loaded into INTEGER columns, but the original remains intact in raw.
    """
    source_field = spec.column
    raw = row.get(spec.column)

    if raw is None:
        blob = row.get("raw")
        if isinstance(blob, dict) and blob.get(spec.column) is not None:
            raw = blob.get(spec.column)
            source_field = f"raw.{spec.column}"

    count, state = _coerce_count(raw, spec.kind)

    age_minutes = None
    if observed_at is not None:
        age_minutes = max(0.0, (now - observed_at).total_seconds() / 60)

    freshness = classify_freshness(age_minutes, buckets=freshness_buckets)

    # If the update exceeds the threshold, escalate to "untrustworthy" regardless of the value (DATA-006).
    if state is not DataState.UNKNOWN and age_minutes is not None and age_minutes > stale_after_minutes:
        state = DataState.STALE

    return ResourceReading(
        spec=spec,
        raw_value=raw,
        count=count,
        state=state,
        freshness=freshness,
        observed_at=observed_at,
        age_minutes=age_minutes,
        source_field=source_field,
        # Keep the fact that it was negative even if it was escalated to STALE due to update delay.
        queue_depth=-count if count is not None and count < 0 else None,
    )


# ---------------------------------------------------------------------------
# Score components
# ---------------------------------------------------------------------------


@dataclass
class ScoreComponent:
    """One of the 5 score elements. Carries "how the number was produced" along with the score."""

    key: str
    label: str
    score: float
    max_score: float
    formula: str = ""  # Formula string with actual values substituted
    inputs: dict[str, Any] = field(default_factory=dict)
    evidences: list[Evidence] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return self.score / self.max_score if self.max_score else 0.0

    def add(self, evidence: Evidence) -> None:
        self.evidences.append(evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "score": round(self.score, 2),
            "max_score": self.max_score,
            "display": f"{round(self.score)}/{round(self.max_score)}",
            "ratio": round(self.ratio, 4),
            "formula": self.formula,
            "inputs": self.inputs,
            "notes": self.notes,
            "evidences": [e.to_dict() for e in self.evidences],
        }


# ---------------------------------------------------------------------------
# Filter decisions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilterOutcome:
    """Decision result for one first-pass filter rule from design doc §5.2."""

    rule: str  # F-01 ~ F-07
    label: str
    action: str  # PASS | EXCLUDE | DOWNGRADE | WARN
    detail: str
    evidences: tuple[Evidence, ...] = ()

    @property
    def excluded(self) -> bool:
        return self.action == "EXCLUDE"

    @property
    def downgraded(self) -> bool:
        return self.action == "DOWNGRADE"

    @property
    def deprioritized(self) -> bool:
        """Decision that keeps the candidate but pushes it down the ranking (-6 or below, slide 22)."""
        return self.action == "DEPRIORITIZE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "label": self.label,
            "action": self.action,
            "detail": self.detail,
            "evidences": [e.to_dict() for e in self.evidences],
        }


# ---------------------------------------------------------------------------
# Common formula helpers
# ---------------------------------------------------------------------------


def log_scale(count: int, *, cap: int = 6) -> float:
    """Design doc §5.3.1 - min(1, log(1+n)/log(cap)).

    Compresses with a log so 5+ beds don't produce an excessive advantage.
    (With cap=6, n=5 gives exactly 1.0.)
    """
    if count <= 0:
        return 0.0
    return min(1.0, math.log(1 + count) / math.log(cap))


def fmt(value: Any, digits: int = 1) -> str:
    """Number formatting for formula strings. Tidies only the fractional part and leaves the integer part alone."""
    if value is None:
        return "없음"
    if isinstance(value, float):
        text = f"{value:.{digits}f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text
    return str(value)


# ===========================================================================
# 3. Scoring engine - filters F-01~F-07, 5-element score, grade, ranking
# ===========================================================================


class Grade(StrEnum):
    """Safety rules from design doc §5.3."""

    A = "A_RECOMMENDABLE"
    B = "B_CONFIRM_REQUIRED"
    EXCLUDED = "EXCLUDE"


# ---------------------------------------------------------------------------
# Configuration - all of these are operational parameters
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoringConfig:
    """Design doc §1.2 - weights, radii and thresholds are operational parameters, not legal or medical standards.

    Everything is pulled out here so it can be changed even during a presentation.

    The weights change wholesale per mode (:data:`MODE_WEIGHTS`). Don't construct this
    directly; build it with :meth:`for_mode` so the mode and weights never drift apart.
    """

    # Score allocation (sums to 100). Defaults are normal mode = travel-time-centric.
    mode: ScoringMode = ScoringMode.NORMAL
    w_clinical: float = 20.0
    w_availability: float = 10.0
    w_eta: float = 50.0
    w_center: float = 10.0
    w_freshness: float = 7.0
    w_stability: float = 3.0

    # Clinical fit bands (the 35/20/0 from §5.3.1 converted to ratios).
    # As absolute points they would exceed the maximum when the mode changes.
    clinical_ratios: tuple[float, float, float] = (1.0, 0.55, 0.0)  # all confirmed / partly unconfirmed / mismatch

    # Internal allocation of the availability proxy (§5.3.1: related beds 15 + ER 5 + OR 5)
    related_bed_points: float = 15.0
    er_points: float = 5.0
    or_points: float = 5.0
    log_cap: int = 6  # min(1, log(1+n)/log(6))
    normalize_availability: bool = True  # When surgery isn't needed, rescale the 20-point max to the full max

    # Whether to use ER beds as a proxy when the required beds are null (§5.3.1
    # "normalize in order: required beds > ER beds > operating rooms")
    unknown_bed_fallback: bool = True

    # --- Zero / negative bed handling (slide 22) ---------------------------
    # 0 is a minor penalty, -1~-5 is a graduated penalty per person, -6 or below is deprioritized.
    # None of them remove the candidate. Removing them creates hospitals that "can treat but don't show up".
    zero_bed_penalty: float = 0.15
    queue_penalty_per_person: float = 0.12
    backlog_queue_threshold: int = 6
    backlog_penalty: float = 0.85

    # Freshness bands (minutes). Finalize after confirming the actual API update interval.
    freshness_buckets: tuple[int, int, int] = (10, 30, 60)
    freshness_ratios: tuple[float, float, float, float] = (1.0, 0.7, 0.3, 0.0)
    stale_after_minutes: int = 60

    # Route & weather stability (full / 60% / 0)
    stability_ratios: tuple[float, float, float] = (1.0, 0.6, 0.0)
    heavy_rain_mm: float = 15.0
    slow_section_kmh: float = 15.0

    # Candidate count / radius (§5.5, LOC-002). Slide 21 - expand 5 → 10 → 20 km.
    max_results: int = 3
    prefilter_candidates: int = 20
    route_candidates: int = 10
    radius_steps_km: tuple[float, ...] = (5.0, 10.0, 20.0)

    rule_version: str = "rule-v2.0"

    @property
    def weights(self) -> dict[str, float]:
        """Lets the screen show "what this mode looks at, and at what %" as-is."""
        return {
            "clinical_fit": self.w_clinical,
            "availability": self.w_availability,
            "eta_traffic": self.w_eta,
            "center_capability": self.w_center,
            "freshness": self.w_freshness,
            "route_weather_stability": self.w_stability,
        }

    @property
    def resource_weight(self) -> float:
        """The bundle the slides call "resources" = clinical fit + availability."""
        return self.w_clinical + self.w_availability

    @property
    def version_label(self) -> str:
        return f"{self.rule_version}-{self.mode.value}"

    @classmethod
    def for_mode(cls, mode: ScoringMode | str, **overrides: Any) -> ScoringConfig:
        """Build a config with the weights for the given mode applied."""
        resolved = ScoringMode(mode)
        return cls(mode=resolved, **MODE_WEIGHTS[resolved], **overrides)


# Weight table from slide 22.
#   Normal : travel 50 · resources 30 (fit 20 + availability 10) · center 10 · rest 10
#   Severe : travel 30 · resources 40 (fit 25 + availability 15) · center 20 · rest 10
# "Rest 10" is data freshness 7 + route & weather stability 3. Both modes sum to 100.
MODE_WEIGHTS: dict[ScoringMode, dict[str, float]] = {
    ScoringMode.NORMAL: {
        "w_clinical": 20.0,
        "w_availability": 10.0,
        "w_eta": 50.0,
        "w_center": 10.0,
        "w_freshness": 7.0,
        "w_stability": 3.0,
    },
    ScoringMode.SEVERE: {
        "w_clinical": 25.0,
        "w_availability": 15.0,
        "w_eta": 30.0,
        "w_center": 20.0,
        "w_freshness": 7.0,
        "w_stability": 3.0,
    },
}

MODE_LABELS: dict[ScoringMode, str] = {
    ScoringMode.NORMAL: "일반 모드 (이동 시간 중심)",
    ScoringMode.SEVERE: "중증 모드 (치료 역량 중심)",
}

DEFAULT_CONFIG = ScoringConfig.for_mode(ScoringMode.NORMAL)
SEVERE_CONFIG = ScoringConfig.for_mode(ScoringMode.SEVERE)


# ---------------------------------------------------------------------------
# Evaluation result
# ---------------------------------------------------------------------------


@dataclass
class CandidateEvaluation:
    """Full evaluation result for one hospital."""

    hpid: str
    duty_name: str | None
    row: dict[str, Any] = field(repr=False, default_factory=dict)
    profile_key: str = ""

    grade: Grade = Grade.B
    total_score: float = 0.0
    components: dict[str, ScoreComponent] = field(default_factory=dict)
    filters: list[FilterOutcome] = field(default_factory=list)
    readings: dict[str, ResourceReading] = field(default_factory=dict, repr=False)

    eta_min: float | None = None
    distance_km: float | None = None
    route_distance_km: float | None = None
    age_minutes: float | None = None
    center_tier: int = 0
    warnings: list[str] = field(default_factory=list)
    rank: int | None = None
    is_best: bool = False

    # Slide 22 - if required beds are -6 or below (excessive queue), keep as a candidate but push it back.
    deprioritized: bool = False
    # Summary of zero / negative readings for required beds. The screen tiles and reasoning text use the same values.
    bed_signal: dict[str, Any] = field(default_factory=dict)

    # Unconfirmed / mismatched resources (for display)
    unconfirmed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    satisfied: list[str] = field(default_factory=list)
    unsupported_equipment: list[str] = field(default_factory=list)

    @property
    def excluded(self) -> bool:
        return self.grade is Grade.EXCLUDED

    def component(self, key: str) -> ScoreComponent:
        return self.components[key]

    def score_of(self, key: str) -> float:
        comp = self.components.get(key)
        return comp.score if comp else 0.0

    def all_evidences(self) -> list[Evidence]:
        out: list[Evidence] = []
        for comp in self.components.values():
            out.extend(comp.evidences)
        for outcome in self.filters:
            out.extend(outcome.evidences)
        return out

    def sort_key(self) -> tuple:
        """Order by total score. Only on ties are the design doc §5.3 priorities used to break them.

        The screen shows the "overall score", so if the ranking disagrees with that number
        the reasoning can't be trusted. Previously the items were compared lexicographically,
        so being ahead by just 0.1 on an earlier item beat a 20-point gap on a later one
        (the cause of #1 at 74 points / #2 at 91 points for chest pain near Daecheong Station).

        There is a cost: the confirmed/unconfirmed difference in clinical_fit (35-point max)
        is 15 points, so if the ETA or availability gap is larger than that, an institution
        with "unconfirmed resources" can rank higher. This actually happens with severe burns,
        where a 1 km institution that doesn't report burn ICU beds beats a 9 km one that does
        (caught by "임상 적합성이 짧은 ETA 를 이긴다" in run_self_test). That is why unconfirmed
        institutions must keep being shown on screen as Grade B "phone confirmation needed".

        The leading deprioritized flag is slide 22's "-6 or below goes to lower priority".
        It places the candidate one notch back independently of the score, so unlike a score
        penalty, other items cannot offset it.
        """
        return (
            self.deprioritized,
            -self.total_score,
            -self.score_of("clinical_fit"),
            -self.score_of("availability"),
            -self.score_of("center_capability"),
            -self.score_of("eta_traffic"),
            -self.score_of("freshness"),
            -self.score_of("route_weather_stability"),
            -self.center_tier,
            self.distance_km if self.distance_km is not None else float("inf"),
        )


# ---------------------------------------------------------------------------
# Stage 0: Reading values
# ---------------------------------------------------------------------------


def _observed_at(row: dict[str, Any]) -> datetime | None:
    value = row.get("hvidate") or row.get("updated_at") or row.get("collected_at")
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=KST)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=KST)


def read_all(
    row: dict[str, Any],
    profile: ResourceProfile,
    *,
    now: datetime,
    config: ScoringConfig,
) -> dict[str, ResourceReading]:
    """Read all resources required by the profile + the common resources needed for the availability calculation."""
    observed = _observed_at(row)
    columns = {req.column for req in profile.requirements} | {"hvec", "hvoc", "hvs01"}

    readings: dict[str, ResourceReading] = {}
    for column in columns:
        spec = resolve_spec(column)
        if spec is None or not spec.supported:
            continue
        readings[column] = read_resource(
            row,
            spec,
            observed_at=observed,
            now=now,
            stale_after_minutes=config.stale_after_minutes,
            freshness_buckets=config.freshness_buckets,
        )
    return readings


# ---------------------------------------------------------------------------
# Stage 1: Filters F-01 ~ F-06 (F-07 is at the recommend level)
# ---------------------------------------------------------------------------


def apply_filters(
    row: dict[str, Any],
    profile: ResourceProfile,
    readings: dict[str, ResourceReading],
    *,
    age_minutes: float | None,
    config: ScoringConfig,
) -> list[FilterOutcome]:
    """Implements the design doc §5.2 table as-is."""
    outcomes: list[FilterOutcome] = []

    # F-01 Hospital identification: are hospital_id / hospital_name / coordinates valid?
    hpid = row.get("hpid")
    name = row.get("duty_name")
    lat, lon = row.get("latitude"), row.get("longitude")
    identified = bool(hpid and name and lat is not None and lon is not None)
    outcomes.append(
        FilterOutcome(
            rule="F-01",
            label="병원 식별",
            action="PASS" if identified else "EXCLUDE",
            detail=(
                f"hpid={hpid}, 병원명={name}, 좌표=({fmt(lat, 5)}, {fmt(lon, 5)})"
                if identified
                else "식별자 또는 좌표가 없어 지도·경로 계산이 불가능하다."
            ),
            evidences=(
                Evidence(
                    code="EV-ID",
                    label=f"{name} ({hpid})",
                    detail=f"좌표 ({fmt(lat, 5)}, {fmt(lon, 5)})",
                    column="hpid",
                    canonical="hospital_id",
                    raw_value=hpid,
                    source="NMC",
                ),
            ),
        )
    )

    # F-02 Emergency center: is the emergency institution classification valid?
    emcls = row.get("duty_emcls_name")
    tier = center_tier(emcls)
    outcomes.append(
        FilterOutcome(
            rule="F-02",
            label="응급센터 분류",
            action="PASS" if tier > 0 else "DOWNGRADE",
            detail=(
                f"{emcls} (등급 {tier}/4)"
                if tier > 0
                else "응급의료기관 분류가 비어 있어 일반 의료기관 혼입 가능성이 있다."
            ),
            evidences=(
                Evidence(
                    code="EV-CENTER",
                    label=emcls or "응급의료기관 분류 없음",
                    detail=f"등급 {tier}/4",
                    column="duty_emcls_name",
                    canonical="emergency_center_type_name",
                    raw_value=emcls,
                    state=DataState.AVAILABLE if tier > 0 else DataState.UNKNOWN,
                    source="NMC",
                ),
            ),
        )
    )

    # F-03 Required resource check. Follows the slide 21/22 policy as-is.
    #   Equipment (FLAG): if 'N', exclude. However close it is, a procedure that can't be done can't be done.
    #   Beds (COUNT): not excluded even if 0 or negative. 0 is a minor penalty, -1~-5 is a graduated
    #                 penalty, -6 or below is deprioritized. score_availability computes the penalty;
    #                 only the decision is recorded here.
    # F-05 No information: null is not excluded, it becomes Grade B
    for req in profile.required():
        reading = readings.get(req.column)
        if reading is None:
            continue
        ev = reading.to_evidence(required=True)
        is_equipment = reading.spec.kind is ResourceKind.FLAG

        if reading.is_empty and is_equipment:
            outcomes.append(
                FilterOutcome(
                    rule="F-03",
                    label=f"필수 장비 미보유: {req.display_label}",
                    action="EXCLUDE",
                    detail=(
                        f"{req.display_label}({req.column})=N. 증상에 필요한 필수 장비가 없어 "
                        "거리와 무관하게 제외한다."
                    ),
                    evidences=(ev,),
                )
            )
        elif reading.is_empty:
            outcomes.append(
                FilterOutcome(
                    rule="F-03",
                    label=f"필수 병상 가용 0: {req.display_label}",
                    action="PENALIZE",
                    detail=(
                        f"{req.display_label}({req.column})=0. 가용 병상이 없다는 보고지만 "
                        "수용 불가 확정이 아니므로 후보로 남기고 경미하게 감점한다."
                    ),
                    evidences=(ev,),
                )
            )
        elif reading.is_queued:
            backlogged = reading.backlogged(config.backlog_queue_threshold)
            # Severe mode uses a different standard. As long as the required treatment resources
            # exist, keep the candidate even with a long queue — queues can shrink, but missing
            # treatment resources won't appear.
            if backlogged and config.mode is ScoringMode.SEVERE:
                outcomes.append(
                    FilterOutcome(
                        rule="F-03",
                        label=f"필수 병상 대기 과다(중증 모드 유지): {req.display_label}",
                        action="PENALIZE",
                        detail=(
                            f"{req.display_label}({req.column})={reading.count} "
                            f"(대기 {reading.queue_depth}명 추정). 중증 모드라 치료 자원이 있는 한 "
                            "후순위로 내리지 않고 감점만 한다."
                        ),
                        evidences=(ev,),
                    )
                )
            else:
                outcomes.append(
                    FilterOutcome(
                        rule="F-03",
                        label=(
                            f"필수 병상 대기 과다: {req.display_label}"
                            if backlogged
                            else f"필수 병상 대기 발생: {req.display_label}"
                        ),
                        action="DEPRIORITIZE" if backlogged else "PENALIZE",
                        detail=(
                            f"{req.display_label}({req.column})={reading.count}. 음수는 가용 없음이 "
                            f"아니라 대기 인원({reading.queue_depth}명) 표기로 본다. "
                            + (
                                f"임계 {config.backlog_queue_threshold}명 이상이라 후순위로 내린다."
                                if backlogged
                                else "후보로 남기고 대기 인원만큼 단계적으로 감점한다."
                            )
                        ),
                        evidences=(ev,),
                    )
                )
        elif reading.state is DataState.UNKNOWN:
            outcomes.append(
                FilterOutcome(
                    rule="F-05",
                    label=f"필수 자원 정보 없음: {req.display_label}",
                    action="DOWNGRADE",
                    detail=(
                        f"{req.display_label}({req.column})=null. '정보 없음'을 '가용 없음'으로 "
                        "오판하지 않고 전화 확인 후보(B등급)로 남긴다."
                    ),
                    evidences=(ev,),
                )
            )
        elif reading.state is DataState.STALE:
            outcomes.append(
                FilterOutcome(
                    rule="F-06",
                    label=f"필수 자원 갱신 지연: {req.display_label}",
                    action="DOWNGRADE",
                    detail=(
                        f"{req.display_label}({req.column}) 값은 있으나 "
                        f"{fmt(reading.age_minutes, 0)}분 전 데이터라 신뢰 구간을 벗어났다."
                    ),
                    evidences=(ev,),
                )
            )
        else:
            outcomes.append(
                FilterOutcome(
                    rule="F-03",
                    label=f"필수 자원 확인: {req.display_label}",
                    action="PASS",
                    detail=f"{req.display_label}({req.column})={reading.value_text()}",
                    evidences=(ev,),
                )
            )

    # F-04 Required equipment: equipment that can't be judged from current data is not used as a filter (§2.4).
    # Never EXCLUDE, but since it's "needs confirmation", downgrade to Grade B. For the same reason the
    # clinical fit score falls into the "partly unconfirmed (20 points)" band, so grade and score stay consistent.
    if profile.unsupported_equipment:
        labels = ", ".join(profile.unsupported_equipment)
        outcomes.append(
            FilterOutcome(
                rule="F-04",
                label="장비 데이터 미보유",
                action="DOWNGRADE",
                detail=(
                    f"{labels} 는 원천 데이터에 없어 필터 조건으로 쓸 수 없다. "
                    "장비 있음/없음을 추정하지 않고 '장비 정보 확인 필요'로만 표시한다."
                ),
            )
        )

    # F-06 Data age: update time of the whole row
    freshness = classify_freshness(age_minutes, buckets=config.freshness_buckets)
    if freshness is Freshness.STALE:
        outcomes.append(
            FilterOutcome(
                rule="F-06",
                label="데이터 경과",
                action="DOWNGRADE",
                detail=f"갱신 후 {fmt(age_minutes, 0)}분 경과 (임계 {config.stale_after_minutes}분).",
                evidences=(
                    Evidence(
                        code="EV-STALE",
                        label=f"{fmt(age_minutes, 0)}분 전 갱신",
                        detail=f"임계 {config.stale_after_minutes}분 초과",
                        column="hvidate",
                        canonical="updated_at",
                        state=DataState.STALE,
                        source="NMC",
                        age_minutes=age_minutes,
                    ),
                ),
            )
        )

    return outcomes


# ---------------------------------------------------------------------------
# Stage 2: Clinical & resource fit (35 points)
# ---------------------------------------------------------------------------


def score_clinical_fit(
    profile: ResourceProfile,
    readings: dict[str, ResourceReading],
    *,
    center_rank: int,
    config: ScoringConfig,
) -> ScoreComponent:
    """Design doc §5.3.1 - all required resources confirmed 35 / partly unconfirmed 20 / mismatch 0.

    The headline score uses these 3 bands as-is. Since that alone can't explain "why
    20 points", the per-resource fulfillment rate is also computed using the user
    table's points (default 20) and kept as evidence. The fulfillment rate doesn't
    change the score; it is only used for tie-breaking and sentence generation.
    """
    comp = ScoreComponent(
        key="clinical_fit", label="임상·자원 적합성", score=0.0, max_score=config.w_clinical
    )

    satisfied: list[str] = []
    unconfirmed: list[str] = []
    mismatched: list[str] = []
    # Resources whose value is confirmed but have no spare capacity (0 or negative). Penalized in availability, not fit.
    tight: list[str] = []
    # Column lists in the same order as the labels. Used when building reason chips to filter out
    # resources like ER and OR that the availability side already talks about.
    satisfied_columns: list[str] = []
    unconfirmed_columns: list[str] = []
    mismatched_columns: list[str] = []
    earned = 0
    possible = 0

    for req in profile.requirements:
        reading = readings.get(req.column)
        if reading is None:
            spec = resolve_spec(req.column)
            if spec is not None and not spec.supported:
                unconfirmed.append(req.display_label)
                unconfirmed_columns.append(req.column)
                comp.add(
                    Evidence(
                        code="EV-RES-UNSUPPORTED",
                        label=f"[{'필수' if req.is_required else '옵션'}] {req.display_label} 판정 불가",
                        detail="원천 데이터에 해당 컬럼이 없다 (설계서 §2.4).",
                        column=req.column,
                        state=DataState.UNKNOWN,
                        source="RULE",
                    )
                )
            continue

        comp.add(reading.to_evidence(required=req.is_required))

        if req.is_required:
            possible += req.weight

        if reading.is_available:
            satisfied.append(f"{req.display_label} {reading.value_text()}")
            satisfied_columns.append(req.column)
            if req.is_required:
                earned += req.weight
        elif reading.is_empty and reading.spec.kind is ResourceKind.FLAG:
            # Equipment 'N' means the procedure can't be done, so it's a true mismatch.
            mismatched.append(req.display_label)
            mismatched_columns.append(req.column)
        elif reading.is_empty or reading.is_queued:
            # Beds at 0 or negative are a report that "we operate this resource but have no spare capacity now".
            # The resource itself is confirmed, so keep the fit band and penalize in availability instead.
            satisfied.append(f"{req.display_label} {reading.value_text()}")
            satisfied_columns.append(req.column)
            tight.append(f"{req.display_label} {reading.value_text()}")
            if req.is_required:
                earned += req.weight
        else:
            unconfirmed.append(req.display_label)
            unconfirmed_columns.append(req.column)

    required_readings = [
        readings[r.column] for r in profile.required() if r.column in readings
    ]
    has_mismatch = any(
        r.is_empty and r.spec.kind is ResourceKind.FLAG for r in required_readings
    )
    has_unknown = any(r.is_unknown for r in required_readings) or bool(
        profile.unsupported_equipment
    )

    full_ratio, partial_ratio, mismatch_ratio = config.clinical_ratios
    if has_mismatch:
        comp.score = config.w_clinical * mismatch_ratio
        tier_text = "명백한 불일치"
    elif has_unknown or not required_readings:
        comp.score = config.w_clinical * partial_ratio
        tier_text = "일부 미확인"
    else:
        comp.score = config.w_clinical * full_ratio
        tier_text = "모든 필수 자원 확인"

    fit_ratio = (earned / possible) if possible else 0.0
    comp.formula = (
        f"구간 판정 = {tier_text} → {fmt(comp.score, 0)} / {fmt(config.w_clinical, 0)}점"
        f"  (필수 자원 충족 배점 {earned}/{possible} = {fit_ratio * 100:.0f}%)"
    )
    comp.inputs = {
        "profile": profile.key,
        "profile_label": profile.label,
        "tier": tier_text,
        "required_columns": [r.column for r in profile.required()],
        "optional_columns": [r.column for r in profile.optional()],
        "satisfied": satisfied,
        "unconfirmed": unconfirmed,
        "mismatched": mismatched,
        "tight": tight,
        "satisfied_columns": satisfied_columns,
        "unconfirmed_columns": unconfirmed_columns,
        "mismatched_columns": mismatched_columns,
        "required_weight_earned": earned,
        "required_weight_total": possible,
        "fit_ratio": round(fit_ratio, 4),
        "center_tier": center_rank,
    }

    if profile.caution:
        comp.notes.append(profile.caution)
    if profile.prefer_high_tier_center:
        comp.notes.append(
            f"고등급 응급센터 우선 대상 - 현재 등급 {center_rank}/4. "
            "등급 점수는 '응급센터 역량' 항목에서 따로 매긴다."
        )
    if mismatched:
        comp.notes.append(f"불일치 장비: {', '.join(mismatched)} → 순위 계산 전에 제외된다.")
    if unconfirmed:
        comp.notes.append(f"미확인 자원: {', '.join(unconfirmed)} → 전화 확인이 필요하다.")
    if tight:
        comp.notes.append(
            f"여유 없음: {', '.join(tight)}. 자원 자체는 확인됐으므로 적합성은 유지하고 "
            "가용성 점수에서 감점한다 (0·음수 처리 정책)."
        )

    return comp


# ---------------------------------------------------------------------------
# Stage 3: Availability proxy (25 points)
# ---------------------------------------------------------------------------


def score_availability(
    profile: ResourceProfile,
    readings: dict[str, ResourceReading],
    *,
    config: ScoringConfig,
) -> ScoreComponent:
    """Design doc §5.3.1 - related beds 15 + ER up to 5 + (if surgery needed) OR up to 5.

    Follows "normalize in order: required beds > ER beds > operating rooms; distinguish
    0/null" as-is. If the required beds are null, ER beds are used as a proxy, but the
    fact that a proxy was used is recorded in the evidence and the grade is not raised
    (the grade has already been lowered to B on the clinical fit side).
    """
    comp = ScoreComponent(
        key="availability", label="가용성 대리값", score=0.0, max_score=config.w_availability
    )

    bed_reqs = profile.bed_requirements()
    related_total = 0
    counted: list[str] = []
    unknown_beds: list[str] = []
    zero_beds: list[str] = []
    queued_beds: list[tuple[str, int]] = []

    for req in bed_reqs:
        reading = readings.get(req.column)
        if reading is None:
            continue
        if reading.is_available:
            value = reading.count or 0
            # FLAG resources (VENTI, incubator) count "available" as 1.
            related_total += value
            counted.append(f"{req.display_label} {reading.value_text()}")
        elif reading.is_empty:
            zero_beds.append(req.display_label)
        elif reading.is_queued:
            queued_beds.append((req.display_label, reading.queue_depth or 0))
        elif reading.is_unknown:
            unknown_beds.append(req.display_label)
        comp.add(reading.to_evidence(required=req.is_required))

    er = readings.get("hvec")
    base = readings.get("hvs01")
    surgery = readings.get("hvoc")

    fallback_used = False
    if related_total == 0 and unknown_beds and config.unknown_bed_fallback:
        # Required beds are null → use ER beds as a proxy (§5.3.1 normalization priority)
        if er is not None and er.is_available:
            related_total = er.count or 0
            fallback_used = True
        elif surgery is not None and surgery.is_available:
            related_total = surgery.count or 0
            fallback_used = True

    related_ratio = log_scale(related_total, cap=config.log_cap)
    related_score = config.related_bed_points * related_ratio

    # ER spare ratio: hvec relative to hvs01 (reference beds)
    er_score = 0.0
    er_formula = "정보 없음 → 0"
    if er is not None and er.is_available:
        er_count = er.count or 0
        if base is not None and base.is_available and (base.count or 0) > 0:
            er_ratio = min(1.0, er_count / (base.count or 1))
            er_score = config.er_points * er_ratio
            er_formula = (
                f"{fmt(config.er_points, 0)} × min(1, hvec {er_count} / hvs01 {base.count})"
                f" = {er_score:.1f}"
            )
        else:
            er_ratio = log_scale(er_count, cap=config.log_cap)
            er_score = config.er_points * er_ratio
            er_formula = (
                f"{fmt(config.er_points, 0)} × min(1, log(1+{er_count})/log({config.log_cap}))"
                f" = {er_score:.1f}  (기준병상 hvs01 없음 → 로그 정규화)"
            )
    elif er is not None and er.is_empty:
        er_formula = "hvec=0 → 0 (여유 없음)"
    elif er is not None and er.state is DataState.STALE:
        er_formula = f"{fmt(er.age_minutes, 0)}분 전 값 → 0 (갱신 지연으로 신뢰 불가)"

    if er is not None:
        comp.add(er.to_evidence(required=False))
    if base is not None and base.is_available:
        comp.add(base.to_evidence(required=False))

    or_score = 0.0
    or_formula = ""
    raw_max = config.related_bed_points + config.er_points
    if profile.needs_surgery:
        raw_max += config.or_points
        if surgery is not None and surgery.is_available:
            or_ratio = log_scale(surgery.count or 0, cap=config.log_cap)
            or_score = config.or_points * or_ratio
            or_formula = (
                f"{fmt(config.or_points, 0)} × min(1, log(1+{surgery.count})/log({config.log_cap}))"
                f" = {or_score:.1f}"
            )
            comp.add(surgery.to_evidence(required=True))
        else:
            or_formula = "수술실 정보 없음/0 → 0"
            if surgery is not None:
                comp.add(surgery.to_evidence(required=True))
    else:
        or_formula = "수술 필요 없음 → 수술실 점수 제외"

    raw_score = related_score + er_score + or_score

    # --- Slide 22: graduated penalty for zero / negative beds ----------------
    # log_scale already turns both 0 and negatives into 0 points, but that alone makes
    # "a place with 0" and "a place with 8 waiting" score the same. Split them once more with a multiplier to create an order.
    deepest_queue = max((depth for _, depth in queued_beds), default=0)
    backlogged = deepest_queue >= config.backlog_queue_threshold
    penalty = 0.0
    penalty_text = ""
    if backlogged:
        penalty = config.backlog_penalty
        penalty_text = (
            f"대기 {deepest_queue}명(임계 {config.backlog_queue_threshold}명 이상) → "
            f"×{1 - penalty:.2f} 후순위"
        )
    elif queued_beds:
        # A place with a queue must always be penalized more than one reporting 0 available, or the order flips.
        # So start from the zero-bed penalty and add per person waiting.
        penalty = min(
            0.9, config.zero_bed_penalty + config.queue_penalty_per_person * deepest_queue
        )
        penalty_text = (
            f"가용 0 감점 {config.zero_bed_penalty:.2f} + 대기 {deepest_queue}명 × "
            f"{config.queue_penalty_per_person:.2f} → ×{1 - penalty:.2f}"
        )
    elif zero_beds:
        penalty = config.zero_bed_penalty
        penalty_text = f"가용 0 → ×{1 - penalty:.2f} (경미)"

    if config.normalize_availability and raw_max > 0:
        comp.score = config.w_availability * (raw_score / raw_max)
        norm_text = f" → {fmt(raw_max, 0)}점 만점을 {fmt(config.w_availability, 0)}점으로 환산"
    else:
        comp.score = min(raw_score, config.w_availability)
        norm_text = ""

    if penalty:
        comp.score *= 1 - penalty
        norm_text += f" → 병상 상태 감점 {penalty_text}"

    related_formula = (
        f"{fmt(config.related_bed_points, 0)} × min(1, log(1+{related_total})/"
        f"log({config.log_cap})) = {related_score:.1f}"
    )
    if fallback_used:
        related_formula += "  (필요 병상 null → 응급실 병상으로 대리 산정)"

    comp.formula = (
        f"관련병상 {related_formula} + 응급실 {er_formula}"
        + (f" + 수술실 {or_formula}" if profile.needs_surgery else "")
        + f" = {raw_score:.1f}{norm_text} → {comp.score:.1f}"
    )
    comp.inputs = {
        "related_bed_total": related_total,
        "related_bed_columns": [r.column for r in bed_reqs],
        "counted": counted,
        "unknown_beds": unknown_beds,
        "zero_beds": zero_beds,
        "queued_beds": [{"label": label, "queue": depth} for label, depth in queued_beds],
        "queue_depth": deepest_queue or None,
        "backlogged": backlogged,
        "bed_penalty": round(penalty, 3),
        "fallback_used": fallback_used,
        "hvec": er.count if er else None,
        "hvs01": base.count if base else None,
        "hvoc": surgery.count if surgery else None,
        # Keep the state alongside. If stale/null numbers are emitted as reason chips,
        # old values read as if they were current.
        "hvec_state": er.state.value if er else None,
        "hvs01_state": base.state.value if base else None,
        "hvoc_state": surgery.state.value if surgery else None,
        "needs_surgery": profile.needs_surgery,
        "raw_score": round(raw_score, 2),
        "raw_max": raw_max,
    }

    if fallback_used:
        comp.notes.append(
            "필요 병상이 null 이라 응급실 병상으로 대리 산정했다. "
            "가용성 점수는 대리값이며 실제 수용 가능 여부가 아니다."
        )
    if unknown_beds:
        comp.notes.append(f"병상 정보 없음: {', '.join(unknown_beds)}")
    if zero_beds:
        comp.notes.append(
            f"가용 0 보고: {', '.join(zero_beds)}. 진료 불가 확정이 아니므로 후보로 두고 "
            "경미하게 감점했다."
        )
    if queued_beds:
        detail = ", ".join(f"{label} 대기 {depth}명 추정" for label, depth in queued_beds)
        comp.notes.append(
            f"음수 병상 보고: {detail}. 음수는 가용 없음이 아니라 대기 인원 표기로 읽는다."
            + (
                " 대기가 임계를 넘어 순위를 뒤로 미뤘다."
                if backlogged and config.mode is not ScoringMode.SEVERE
                else ""
            )
        )
    comp.notes.append("병상 숫자는 수용 확정이 아니다 (설계서 §5.2 [R7]).")

    return comp


# ---------------------------------------------------------------------------
# Stage 4: Actual arrival time & traffic (25 points)
# ---------------------------------------------------------------------------


def score_eta(
    route: dict[str, Any] | None,
    *,
    min_eta_min: float | None,
    distance_km: float | None,
    config: ScoringConfig,
) -> ScoreComponent:
    """Design doc §5.3.1 - 25 × (minimum ETA / this ETA), max 25.

    If the route API fails: 0 points + a warning. Per design doc §5.7, the candidate
    is kept with a temporary straight-line-distance ordering, and "estimated time
    not available" is stated explicitly.
    """
    comp = ScoreComponent(
        key="eta_traffic", label="실제 도착시간·교통", score=0.0, max_score=config.w_eta
    )

    if not route or route.get("duration_min") is None:
        comp.formula = "경로 조회 실패 → 0 (직선거리 기반 임시 정렬)"
        comp.inputs = {"route": None, "straight_distance_km": distance_km}
        comp.notes.append(
            "TMAP 경로를 받지 못해 예상 소요시간을 제공할 수 없다. "
            "직선거리로만 정렬했고 이 후보는 목록 뒤로 밀린다 (설계서 §5.7)."
        )
        comp.add(
            Evidence(
                code="EV-ROUTE-FAIL",
                label="예상 소요시간 미제공",
                detail=f"직선거리 {fmt(distance_km, 1)}km 기준 임시 정렬",
                source="TMAP",
                state=DataState.UNKNOWN,
            )
        )
        return comp

    eta = float(route["duration_min"])
    route_km = route.get("distance_km")

    if min_eta_min is None or eta <= 0:
        comp.score = config.w_eta
        comp.formula = f"비교 대상 없음 → 만점 {fmt(config.w_eta, 0)}"
    else:
        comp.score = min(config.w_eta, config.w_eta * (min_eta_min / eta))
        comp.formula = (
            f"{fmt(config.w_eta, 0)} × (최소ETA {min_eta_min:.1f}분 / 해당ETA {eta:.1f}분)"
            f" = {comp.score:.1f}"
        )

    comp.inputs = {
        "eta_min": eta,
        "min_eta_min": min_eta_min,
        "route_distance_km": route_km,
        "straight_distance_km": distance_km,
        "traffic_status": route.get("traffic_status"),
        "taxi_fare": route.get("taxi_fare"),
    }
    comp.add(
        Evidence(
            code="EV-ETA",
            label=f"예상 {eta:.0f}분 · {fmt(route_km, 1)}km",
            detail=(
                f"TMAP 실시간 교통 반영. 직선거리 {fmt(distance_km, 1)}km, "
                f"경로거리 {fmt(route_km, 1)}km"
            ),
            canonical="estimated_travel_time_sec",
            raw_value=route.get("duration_s"),
            state=DataState.AVAILABLE,
            source="TMAP",
        )
    )
    if distance_km and route_km and route_km > distance_km * 1.6:
        comp.notes.append(
            f"직선거리({fmt(distance_km, 1)}km) 대비 실제 경로({fmt(route_km, 1)}km)가 길다. "
            "우회 구간이 있는 경로다."
        )
    return comp


# ---------------------------------------------------------------------------
# Stage 5: Center capability (normal 10 points / severe 20 points)
# ---------------------------------------------------------------------------


def score_center_capability(
    duty_emcls_name: str | None,
    *,
    tier: int,
    profile: ResourceProfile,
    config: ScoringConfig,
) -> ScoreComponent:
    """Convert the emergency institution classification into a score ("center capability" on slide 22).

    Previously the tier was only used for tie-breaking, but that doesn't fit severe
    cases. Regional centers and local institutions can perform different procedures,
    and that difference outweighs a few minutes of travel. So in severe mode it is
    allocated 20 points and reflected directly in the ranking.
    """
    comp = ScoreComponent(
        key="center_capability",
        label="응급센터 역량",
        score=0.0,
        max_score=config.w_center,
    )

    max_tier = max(rank for _, rank in CENTER_TIERS)
    comp.score = config.w_center * (tier / max_tier)
    comp.formula = (
        f"{fmt(config.w_center, 0)} × (등급 {tier} / {max_tier}) = {comp.score:.1f}"
        if tier > 0
        else f"응급의료기관 분류 없음 → 0 / {fmt(config.w_center, 0)}"
    )
    comp.inputs = {
        "center_tier": tier,
        "max_tier": max_tier,
        "duty_emcls_name": duty_emcls_name,
        "prefer_high_tier_center": profile.prefer_high_tier_center,
        "mode": config.mode.value,
    }
    comp.add(
        Evidence(
            code="EV-CENTER-SCORE",
            label=duty_emcls_name or "응급의료기관 분류 없음",
            detail=f"등급 {tier}/{max_tier} · 배점 {fmt(config.w_center, 0)}점",
            column="duty_emcls_name",
            canonical="emergency_center_type_name",
            raw_value=duty_emcls_name,
            state=DataState.AVAILABLE if tier > 0 else DataState.UNKNOWN,
            source="NMC",
        )
    )
    if config.mode is ScoringMode.SEVERE:
        comp.notes.append(
            "중증 모드라 센터 역량 배점을 20점으로 올려 잡았다. 이동 시간보다 "
            "치료 역량을 먼저 본다."
        )
    if tier == 0:
        comp.notes.append("응급의료기관 분류를 확인할 수 없어 0점 처리했다 (F-02).")
    return comp


# ---------------------------------------------------------------------------
# Stage 6: Data freshness (7 points)
# ---------------------------------------------------------------------------


def score_freshness(
    age_minutes: float | None,
    *,
    observed_at: datetime | None,
    config: ScoringConfig,
) -> ScoreComponent:
    """Design doc §5.3.1 - 10/7/3/0-point bands (e.g. 10/30/60 minutes)."""
    comp = ScoreComponent(
        key="freshness", label="데이터 최신성", score=0.0, max_score=config.w_freshness
    )
    level = classify_freshness(age_minutes, buckets=config.freshness_buckets)
    fresh, recent, aging = config.freshness_buckets
    # Band points are kept as ratios of the maximum. Even if the maximum (w_freshness)
    # changes per mode, the 10/7/3/0 shape stays the same.
    scale = [config.w_freshness * r for r in config.freshness_ratios]
    points = dict(
        zip(
            (Freshness.FRESH, Freshness.RECENT, Freshness.AGING, Freshness.STALE),
            scale,
            strict=True,
        )
    )
    comp.score = points[level]

    if age_minutes is None:
        comp.formula = "갱신시각 없음 → 0"
    else:
        comp.formula = (
            f"{age_minutes:.0f}분 경과 → 구간 {level.value}"
            f" (≤{fresh}분:{fmt(scale[0], 1)} /"
            f" ≤{recent}분:{fmt(scale[1], 1)} /"
            f" ≤{aging}분:{fmt(scale[2], 1)} /"
            f" 초과:{fmt(scale[3], 1)})"
            f" = {comp.score:.1f}"
        )

    comp.inputs = {
        "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
        "observed_at": observed_at.isoformat() if observed_at else None,
        "level": level.value,
        "buckets_min": list(config.freshness_buckets),
    }
    comp.add(
        Evidence(
            code="EV-FRESHNESS",
            label=(f"{age_minutes:.0f}분 전 갱신" if age_minutes is not None else "갱신시각 없음"),
            detail=f"병원이 입력한 hvidate 기준 · 구간 {level.value}",
            column="hvidate",
            canonical="updated_at",
            raw_value=observed_at.isoformat() if observed_at else None,
            state=DataState.STALE if level is Freshness.STALE else DataState.AVAILABLE,
            source="NMC",
            observed_at=observed_at,
            age_minutes=age_minutes,
        )
    )
    if level in (Freshness.AGING, Freshness.STALE):
        comp.notes.append("갱신이 지연된 데이터다. 이동 전 응급실 전화 확인을 권한다.")
    return comp


# ---------------------------------------------------------------------------
# Stage 6: Route & weather stability (5 points)
# ---------------------------------------------------------------------------

PTY_LABELS = {0: "없음", 1: "비", 2: "비/눈", 3: "눈", 4: "소나기", 5: "빗방울", 6: "진눈깨비", 7: "눈날림"}


def score_stability(
    weather: dict[str, Any] | None,
    route: dict[str, Any] | None,
    *,
    config: ScoringConfig,
) -> ScoreComponent:
    """Design doc §5.3.1 - 5/3/0 points. Used only for tie adjustment; never outranks medical fit."""
    good, mid, bad = (config.w_stability * r for r in config.stability_ratios)
    comp = ScoreComponent(
        key="route_weather_stability",
        label="경로·기상 안정성",
        score=good,
        max_score=config.w_stability,
    )

    penalties: list[str] = []

    pty = (weather or {}).get("pty")
    rain_mm = (weather or {}).get("rn1")
    temp = (weather or {}).get("t1h")

    if weather is None:
        comp.notes.append("기상 데이터가 없어 감점 없이 만점 처리했다.")
    else:
        if rain_mm is not None and float(rain_mm) >= config.heavy_rain_mm:
            penalties.append(f"시간당 강수 {fmt(rain_mm, 1)}mm (폭우 임계 {config.heavy_rain_mm}mm)")
            comp.score = bad
        elif pty not in (None, 0):
            penalties.append(f"강수형태 {PTY_LABELS.get(int(pty), pty)}")
            comp.score = min(comp.score, mid)

        comp.add(
            Evidence(
                code="EV-WEATHER",
                label=(
                    f"{PTY_LABELS.get(int(pty), pty)}"
                    if pty not in (None,)
                    else "강수 정보 없음"
                ),
                detail=f"기온 {fmt(temp, 1)}℃ · 1시간 강수 {fmt(rain_mm, 1)}mm",
                canonical="current_rain_type",
                raw_value=pty,
                state=DataState.AVAILABLE if pty is not None else DataState.UNKNOWN,
                source="KMA",
            )
        )

    sections = (route or {}).get("traffic_sections") or []
    slow = [
        s
        for s in sections
        if isinstance(s, dict)
        and s.get("speed_kmh") is not None
        and float(s["speed_kmh"]) < config.slow_section_kmh
    ]
    status = (route or {}).get("traffic_status")

    if slow:
        penalties.append(f"저속 구간 {len(slow)}개 (<{config.slow_section_kmh}km/h)")
        comp.score = min(comp.score, mid if len(slow) < 3 else bad)
        comp.add(
            Evidence(
                code="EV-TRAFFIC",
                label=f"정체 구간 {len(slow)}곳",
                detail=", ".join(
                    f"{s.get('road_name', '이름없음')} {fmt(s.get('speed_kmh'), 0)}km/h" for s in slow[:3]
                ),
                canonical="traffic_sections",
                state=DataState.AVAILABLE,
                source="TMAP",
            )
        )
    elif status:
        comp.add(
            Evidence(
                code="EV-TRAFFIC",
                label=f"교통 상태 {status}",
                detail="구간별 저속 정보 없음",
                canonical="traffic_status",
                raw_value=status,
                state=DataState.AVAILABLE,
                source="TMAP",
            )
        )
    elif route:
        comp.notes.append(
            "TMAP 응답에 구간별 교통(traffic_sections)이 없어 경로 위험을 판정하지 못했다."
        )

    comp.formula = (
        f"기본 {fmt(good, 1)}점"
        + (" - " + " / ".join(penalties) if penalties else " (감점 사유 없음)")
        + f" = {comp.score:.1f}"
    )
    comp.inputs = {
        "pty": pty,
        "rain_amount_mm": rain_mm,
        "current_temp": temp,
        "slow_sections": len(slow),
        "traffic_status": status,
    }
    comp.notes.append("기상·교통은 동점 조정용이며 병원 자원 부족을 상쇄하지 않는다.")
    return comp


# ---------------------------------------------------------------------------
# Evaluating a single candidate
# ---------------------------------------------------------------------------


def evaluate_candidate(
    row: dict[str, Any],
    *,
    profile: ResourceProfile,
    now: datetime | None = None,
    config: ScoringConfig = DEFAULT_CONFIG,
    route: dict[str, Any] | None = None,
    weather: dict[str, Any] | None = None,
    min_eta_min: float | None = None,
) -> CandidateEvaluation:
    """Evaluate one hospital. The ETA score is relative, so `min_eta_min` is required.

    When called on its own with `min_eta_min=None`, ETA gets full marks, so use
    :func:`recommend` for actual ranking.
    """
    now = now or datetime.now(KST)
    observed = _observed_at(row)
    age_minutes = (now - observed).total_seconds() / 60 if observed else None
    if age_minutes is not None:
        age_minutes = max(0.0, age_minutes)

    readings = read_all(row, profile, now=now, config=config)
    tier = center_tier(row.get("duty_emcls_name"))
    route = route if route is not None else row.get("route")

    evaluation = CandidateEvaluation(
        hpid=str(row.get("hpid") or ""),
        duty_name=row.get("duty_name"),
        row=row,
        profile_key=profile.key,
        readings=readings,
        distance_km=row.get("distance_km"),
        age_minutes=age_minutes,
        center_tier=tier,
    )

    evaluation.filters = apply_filters(
        row, profile, readings, age_minutes=age_minutes, config=config
    )

    evaluation.components["clinical_fit"] = score_clinical_fit(
        profile, readings, center_rank=tier, config=config
    )
    evaluation.components["availability"] = score_availability(profile, readings, config=config)
    evaluation.components["eta_traffic"] = score_eta(
        route, min_eta_min=min_eta_min, distance_km=row.get("distance_km"), config=config
    )
    evaluation.components["center_capability"] = score_center_capability(
        row.get("duty_emcls_name"), tier=tier, profile=profile, config=config
    )
    evaluation.components["freshness"] = score_freshness(
        age_minutes, observed_at=observed, config=config
    )
    evaluation.components["route_weather_stability"] = score_stability(
        weather, route, config=config
    )

    evaluation.total_score = sum(c.score for c in evaluation.components.values())

    if route and route.get("duration_min") is not None:
        evaluation.eta_min = float(route["duration_min"])
        evaluation.route_distance_km = route.get("distance_km")

    # Grade decision (design doc §5.3 safety rules)
    if any(f.excluded for f in evaluation.filters):
        evaluation.grade = Grade.EXCLUDED
    elif any(f.downgraded for f in evaluation.filters):
        evaluation.grade = Grade.B
    else:
        evaluation.grade = Grade.A

    # Slide 22 - an excessive queue means lower priority, not exclusion. Used only for ranking.
    evaluation.deprioritized = any(f.deprioritized for f in evaluation.filters)

    availability_inputs = evaluation.components["availability"].inputs
    evaluation.bed_signal = {
        "zero_beds": list(availability_inputs.get("zero_beds", [])),
        "queued_beds": list(availability_inputs.get("queued_beds", [])),
        "queue_depth": availability_inputs.get("queue_depth"),
        "unknown_beds": list(availability_inputs.get("unknown_beds", [])),
        "penalty": availability_inputs.get("bed_penalty", 0.0),
        "backlogged": bool(availability_inputs.get("backlogged")),
        "deprioritized": evaluation.deprioritized,
    }

    clinical_inputs = evaluation.components["clinical_fit"].inputs
    evaluation.satisfied = list(clinical_inputs.get("satisfied", []))
    evaluation.unconfirmed = list(clinical_inputs.get("unconfirmed", []))
    evaluation.missing = list(clinical_inputs.get("mismatched", []))
    evaluation.unsupported_equipment = list(profile.unsupported_equipment)

    evaluation.warnings = _build_warnings(evaluation)
    return evaluation


def _build_warnings(evaluation: CandidateEvaluation) -> list[str]:
    """Design doc §5.6·§5.7 - warnings that must always be shown on screen alongside the result."""
    out: list[str] = []
    if evaluation.unconfirmed:
        out.append(
            f"{', '.join(evaluation.unconfirmed)} 정보가 확인되지 않았습니다. 전화 확인이 필요합니다."
        )
    if evaluation.missing:
        out.append(f"{', '.join(evaluation.missing)} 장비를 확인할 수 없습니다.")
    zero_beds = evaluation.bed_signal.get("zero_beds") or []
    if zero_beds:
        out.append(
            f"{', '.join(zero_beds)} 가용 병상이 0으로 보고됐습니다. 수용 불가 확정은 "
            "아니지만 출발 전 전화 확인을 권합니다."
        )
    queued = evaluation.bed_signal.get("queued_beds") or []
    if queued:
        detail = ", ".join(f"{q['label']} 대기 {q['queue']}명 추정" for q in queued)
        out.append(
            f"{detail}. 음수로 보고된 값이라 대기 인원으로 해석했습니다."
            + (" 대기가 많아 순위를 뒤로 두었습니다." if evaluation.deprioritized else "")
        )
    if evaluation.unsupported_equipment:
        out.append("일부 장비 정보는 현재 데이터로 확인할 수 없어 병원 확인이 필요합니다.")
    if evaluation.age_minutes is not None and evaluation.age_minutes > 30:
        out.append(f"{evaluation.age_minutes:.0f}분 전 데이터입니다. 현재 상태와 다를 수 있습니다.")
    if evaluation.eta_min is None:
        out.append("경로를 계산하지 못해 예상 소요시간을 제공할 수 없습니다.")
    if evaluation.center_tier == 0:
        out.append("응급의료기관 분류를 확인할 수 없습니다.")
    return out


# ---------------------------------------------------------------------------
# Full recommendation
# ---------------------------------------------------------------------------


@dataclass
class RecommendationResult:
    """Result of one recommendation run."""

    profile: ResourceProfile
    candidates: list[CandidateEvaluation]  # Final candidates shown (up to 3)
    excluded: list[CandidateEvaluation]
    evaluated_count: int
    generated_at: datetime
    config: ScoringConfig
    radius_km: float | None = None
    notes: list[str] = field(default_factory=list)
    # All evaluation results. Used to separately point out institutions that hold the required resources but didn't make the top 3.
    evaluated: list[CandidateEvaluation] = field(default_factory=list, repr=False)
    # The observations actually used for this scoring. The screen must show the same values so the score and weather display don't diverge.
    weather: dict[str, Any] | None = None
    # How many places were seen while widening the radius (slide 21, stage 2). Shown on screen as-is.
    radius_steps: list[dict[str, Any]] = field(default_factory=list)

    @property
    def best(self) -> CandidateEvaluation | None:
        """Top Grade-A candidate. If there is no Grade A, no Best is given at all (slide 21 safety measure)."""
        return next((c for c in self.candidates if c.is_best), None)

    @property
    def has_a_grade(self) -> bool:
        return any(c.grade is Grade.A for c in self.candidates)

    @property
    def mode(self) -> ScoringMode:
        return self.config.mode


def recommend(
    rows: Sequence[dict[str, Any]],
    *,
    profile: ResourceProfile,
    now: datetime | None = None,
    config: ScoringConfig = DEFAULT_CONFIG,
    weather: dict[str, Any] | None = None,
    routes: dict[str, dict[str, Any]] | None = None,
    radius_km: float | None = None,
) -> RecommendationResult:
    """Evaluate the candidate list and return up to 3 places with reasoning.

    rows    : Result of hospitals LEFT JOIN bed_status_latest (the shape find_nearby returns).
              If route is already attached to each row it is used as-is; otherwise pass it via routes.
    routes  : {hpid: tmap route dict}. The result of tmap.attach_routes indexed by hpid.
    weather : Result of weather.get_current_by_latlon (one record for the user's location).
    """
    now = now or datetime.now(KST)
    routes = routes or {}

    # First pass: ETA is a relative score, so the minimum ETA must be found first.
    first_pass: list[CandidateEvaluation] = []
    for row in rows:
        route = routes.get(str(row.get("hpid"))) or row.get("route")
        first_pass.append(
            evaluate_candidate(
                row,
                profile=profile,
                now=now,
                config=config,
                route=route,
                weather=weather,
                min_eta_min=None,
            )
        )

    etas = [c.eta_min for c in first_pass if not c.excluded and c.eta_min is not None]
    min_eta = min(etas) if etas else None

    # Second pass: re-score with the confirmed minimum ETA.
    evaluations: list[CandidateEvaluation] = []
    for row in rows:
        route = routes.get(str(row.get("hpid"))) or row.get("route")
        evaluations.append(
            evaluate_candidate(
                row,
                profile=profile,
                now=now,
                config=config,
                route=route,
                weather=weather,
                min_eta_min=min_eta,
            )
        )

    excluded = [c for c in evaluations if c.excluded]
    survivors = [c for c in evaluations if not c.excluded]
    survivors.sort(key=lambda c: c.sort_key())

    selected = survivors[: config.max_results]
    for index, candidate in enumerate(selected, start=1):
        candidate.rank = index

    notes: list[str] = []
    if config.mode is ScoringMode.SEVERE:
        notes.append(
            "중증 신호가 있어 가중치를 전환했다 (이동 시간 30% / 자원 40% / 센터 역량 20%). "
            "이는 의료진의 KTAS 확정 분류가 아니라 검색 결과의 우선순위를 바꾸는 안전 정책이다."
        )
    a_grade = [c for c in selected if c.grade is Grade.A]
    if selected:
        if a_grade:
            # Best label goes only to the top Grade-A candidate (design doc §5.6)
            a_grade[0].is_best = True
        else:
            notes.append(
                "A등급 후보가 없어 'Best' 대신 '우선 확인 후보'로 표시한다 (설계서 §5.6)."
            )
    if any(c.deprioritized for c in selected):
        notes.append(
            "대기 인원이 많은 것으로 보고된(음수 -6 이하) 기관은 목록에 남기되 후순위로 내렸다."
        )

    # F-07 Not enough candidates: never add unsuitable hospitals just to fill out the count.
    if len(a_grade) < config.max_results:
        notes.append(
            f"A등급 후보가 {len(a_grade)}곳이다. 반경을 확대해 재검색하되, "
            "숫자를 맞추려고 부적합 병원을 포함하지 않는다 (설계서 F-07)."
        )
    if not selected:
        notes.append(
            "적합 후보가 0곳이다. 반경 확대 후에도 없으면 119·응급의료정보센터(1339) "
            "전화 확인을 안내한다 (설계서 §5.7)."
        )
    if min_eta is None and survivors:
        notes.append("경로 API 결과가 없어 전 후보를 직선거리 기준으로 정렬했다 (설계서 §5.7).")

    return RecommendationResult(
        profile=profile,
        candidates=selected,
        excluded=excluded,
        evaluated=evaluations,
        evaluated_count=len(evaluations),
        generated_at=now,
        config=config,
        radius_km=radius_km,
        notes=notes,
        weather=weather,
    )


def recommend_with_radius_expansion(
    fetch_rows: Callable[[float], Sequence[dict[str, Any]]],
    *,
    profile: ResourceProfile,
    config: ScoringConfig = DEFAULT_CONFIG,
    **kwargs: Any,
) -> RecommendationResult:
    """Slide 21, stage 2 - widen the straight-line radius step by step: 5 → 10 → 20 km.

    The reason for filtering by straight-line distance first instead of calling the
    route API from the start is to reduce TMAP call costs. Routes are attached only to
    institutions that pass this stage.

    fetch_rows(radius_km) is a callback that returns the candidate rows within that radius.
    (Just wrap find_nearby.)
    """
    result: RecommendationResult | None = None
    steps: list[dict[str, Any]] = []
    for radius in config.radius_steps_km:
        rows = fetch_rows(radius)
        result = recommend(rows, profile=profile, config=config, radius_km=radius, **kwargs)
        a_count = sum(1 for c in result.candidates if c.grade is Grade.A)
        steps.append({"radius_km": radius, "rows": len(rows), "a_grade": a_count})
        if a_count >= config.max_results:
            break
        if radius != config.radius_steps_km[-1]:
            result.notes.append(
                f"반경 {radius:.0f}km 에서 A등급 후보가 {a_count}곳이라 범위를 넓힌다. "
                "사용자에게 '가까운 곳에 조건을 충족하는 응급실이 없어 범위를 넓혔습니다'라고 알린다."
            )
    assert result is not None
    result.radius_steps = steps
    return result


# ===========================================================================
# 4. Report generation - reason chips, overall opinion, cards, evidence detail, audit log
# ===========================================================================

# Reasons are picked in the same order as the design doc §5.3 tie-break priority.
REASON_PRIORITY: tuple[str, ...] = (
    "clinical_fit",
    "availability",
    "center_capability",
    "eta_traffic",
    "freshness",
    "route_weather_stability",
)


@dataclass(frozen=True)
class Reason:
    """One recommendation reason chip."""

    code: str
    text: str
    component: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "text": self.text, "component": self.component}


# ---------------------------------------------------------------------------
# Recommendation reasons (up to 3)
# ---------------------------------------------------------------------------


def build_reasons(evaluation: CandidateEvaluation, *, limit: int = 3) -> list[Reason]:
    """Design doc §5.6 - up to 3 recommendation reasons.

    The priority is the same as the §5.3 tie-break priority:
    clinical fit > availability > ETA > freshness > route & weather
    """
    buckets: dict[str, list[Reason]] = {key: [] for key in REASON_PRIORITY}

    clinical = evaluation.components["clinical_fit"]
    availability = evaluation.components["availability"]
    center = evaluation.components["center_capability"]
    eta = evaluation.components["eta_traffic"]
    freshness = evaluation.components["freshness"]
    stability = evaluation.components["route_weather_stability"]

    # 1) Clinical fit - if there are confirmed required resources, say those; if unconfirmed, say that first.
    #    ER and OR are excluded here because the availability bucket below already states them with numbers (avoid duplication).
    def _clinical_items(kind: str) -> list[str]:
        labels = clinical.inputs.get(kind, [])
        columns = clinical.inputs.get(f"{kind}_columns", [])
        if len(columns) != len(labels):  # Defensive: the two lists must always have the same length
            columns = labels
        pairs = zip(labels, columns, strict=True)
        return [label for label, column in pairs if column not in {"hvec", "hvoc"}]

    # label already includes the value, e.g. "CT 가용" / "외상 중환자실 3개" (value_text() was
    # appended when building satisfied). Appending '가용' again here would produce "CT 가용 가용".
    for label in _clinical_items("satisfied")[:2]:
        buckets["clinical_fit"].append(Reason("RSN-BED-OK", label, "clinical_fit"))
    for label in _clinical_items("unconfirmed")[:2]:
        buckets["clinical_fit"].append(
            Reason("RSN-BED-UNKNOWN", f"{label} 정보 확인 필요", "clinical_fit")
        )

    # 2) Availability - not used as a reason in two cases.
    #    (a) Proxy estimate (fallback): presenting "응급실 12개 가용" as a reason while the
    #        required resource is unconfirmed reads as if it were confirmed.
    #    (b) Value is stale/null: a number from 92 minutes ago must not be shown as current evidence.
    live = DataState.AVAILABLE.value
    if not availability.inputs.get("fallback_used"):
        er = availability.inputs.get("hvec")
        base = availability.inputs.get("hvs01")
        if er and availability.inputs.get("hvec_state") == live:
            if base and availability.inputs.get("hvs01_state") == live:
                buckets["availability"].append(
                    Reason(
                        "RSN-ER",
                        f"응급실 {er}/{base}자리 ({er / base * 100:.0f}%)",
                        "availability",
                    )
                )
            else:
                buckets["availability"].append(
                    Reason("RSN-ER", f"응급실 {er}자리 가용", "availability")
                )
        if (
            availability.inputs.get("needs_surgery")
            and availability.inputs.get("hvoc")
            and availability.inputs.get("hvoc_state") == live
        ):
            buckets["availability"].append(
                Reason("RSN-OR", f"수술실 {availability.inputs['hvoc']}실 가용", "availability")
            )

    # 2-1) The zero/negative itself is already stated by the clinical fit chip above,
    #      e.g. "화상중환자 대기 3명 추정". Here we only add the fact that it pushed the ranking down.
    if evaluation.deprioritized:
        buckets["availability"].append(
            Reason("RSN-BED-BACKLOG", "대기 많음 · 후순위로 표시", "availability")
        )

    # 3) Center capability - worth 20 points in severe mode, so without it in the reasons the score can't be explained.
    center_tier_value = center.inputs.get("center_tier") or 0
    if center_tier_value >= 3 and center.inputs.get("duty_emcls_name"):
        buckets["center_capability"].append(
            Reason("RSN-CENTER", str(center.inputs["duty_emcls_name"]), "center_capability")
        )

    # 4) ETA
    eta_min = eta.inputs.get("eta_min")
    if eta_min is not None:
        if eta.score >= eta.max_score - 0.01:
            buckets["eta_traffic"].append(
                Reason("RSN-ETA-FASTEST", f"예상 {eta_min:.0f}분 · 후보 중 가장 빠름", "eta_traffic")
            )
        else:
            buckets["eta_traffic"].append(
                Reason("RSN-ETA", f"예상 {eta_min:.0f}분", "eta_traffic")
            )

    # 5) Freshness
    age = freshness.inputs.get("age_minutes")
    if age is not None and freshness.score > 0:
        buckets["freshness"].append(Reason("RSN-FRESH", f"{age:.0f}분 전 갱신", "freshness"))

    # 6) Route & weather
    if stability.score >= stability.max_score - 0.01:
        buckets["route_weather_stability"].append(
            Reason("RSN-ROUTE-OK", "경로·기상 양호", "route_weather_stability")
        )

    reasons: list[Reason] = []
    for key in REASON_PRIORITY:
        for reason in buckets[key]:
            if len(reasons) >= limit:
                return reasons
            reasons.append(reason)
    return reasons


# ---------------------------------------------------------------------------
# Overall opinion
# ---------------------------------------------------------------------------


def build_summary(evaluation: CandidateEvaluation) -> str:
    """The "overall opinion" paragraph at the bottom of the screen.

    Design doc §5.6 - use "based on current data" wording and never assert admission is confirmed.
    """
    parts: list[str] = []

    if evaluation.grade is Grade.EXCLUDED:
        reason = next((f.detail for f in evaluation.filters if f.excluded), "필수 조건 불일치")
        return assert_no_capacity_claim(f"추천 대상에서 제외된 병원입니다. 사유: {reason}")

    if evaluation.missing:
        parts.append(f"{', '.join(evaluation.missing)} 가용 병상이 없습니다.")
    if evaluation.unconfirmed:
        parts.append("필요 자원 정보가 일부 확인되지 않았습니다.")
    if evaluation.unsupported_equipment:
        parts.append("일부 장비 정보는 현재 제공되는 데이터에 없어 병원 확인이 필요합니다.")

    age = evaluation.age_minutes
    if age is not None:
        if age <= 10:
            parts.append(f"병상 정보는 {age:.0f}분 전 갱신된 현재 데이터 기준입니다.")
        elif age <= 60:
            parts.append(f"병상 정보가 {age:.0f}분 전 값이라 현재 상태와 다를 수 있습니다.")
        else:
            parts.append(f"병상 정보가 {age:.0f}분 전 값으로 오래되었습니다.")

    if evaluation.eta_min is not None:
        parts.append(
            f"실시간 교통 기준 예상 소요시간은 {evaluation.eta_min:.0f}분입니다."
        )
    else:
        parts.append("경로를 계산하지 못해 예상 소요시간은 제공되지 않습니다.")

    if evaluation.grade is Grade.B:
        parts.append("이동 전 응급실에 전화해 실제 진료 가능 여부를 확인해 주세요.")
    else:
        parts.append(
            "표시된 병상 수는 수용 확정이 아니므로 출발 전 응급실 전화 확인을 권합니다."
        )

    return assert_no_capacity_claim(" ".join(parts))


# ---------------------------------------------------------------------------
# Screen card
# ---------------------------------------------------------------------------


def build_card(evaluation: CandidateEvaluation) -> dict[str, Any]:
    """Collect every value needed for one recommendation report screen."""
    row = evaluation.row
    availability = evaluation.components["availability"]
    clinical = evaluation.components["clinical_fit"]

    if evaluation.unconfirmed:
        resource_tile = f"{evaluation.unconfirmed[0]} 미확인"
    elif evaluation.missing:
        resource_tile = f"{evaluation.missing[0]} 없음"
    elif clinical.inputs.get("satisfied"):
        resource_tile = clinical.inputs["satisfied"][0]
    else:
        resource_tile = "요구 자원 없음"

    queue_depth = availability.inputs.get("queue_depth")
    if evaluation.grade is Grade.B:
        availability_tile = "전화 확인 필요"
    elif queue_depth:
        availability_tile = f"대기 {queue_depth}명 추정"
    elif availability.inputs.get("zero_beds"):
        availability_tile = "가용 0 보고"
    elif (
        availability.inputs.get("hvec")
        and availability.inputs.get("hvec_state") == DataState.AVAILABLE.value
    ):
        availability_tile = f"응급실 {availability.inputs['hvec']}자리"
    else:
        availability_tile = "확인 필요"

    age = evaluation.age_minutes
    age_text = f"{age:.0f}분 전" if age is not None else "갱신시각 없음"

    if evaluation.eta_min is not None:
        eta_tile = f"{evaluation.eta_min:.0f}분"
        eta_sub = f"{fmt(evaluation.route_distance_km, 1)}km"
    else:
        eta_tile = "계산 실패"
        eta_sub = f"직선 {fmt(evaluation.distance_km, 1)}km"

    return {
        "hpid": evaluation.hpid,
        "rank": evaluation.rank,
        "badge": (
            "Best"
            if evaluation.is_best
            else ("우선 확인 후보" if evaluation.grade is Grade.B else None)
        ),
        "grade": evaluation.grade.value,
        # Value the screen uses to decide whether to attach "needs confirmation", so it doesn't have to parse the grade string.
        "confirm_required": evaluation.grade is Grade.B,
        "deprioritized": evaluation.deprioritized,
        "bed_signal": evaluation.bed_signal,
        "hospital_name": evaluation.duty_name,
        "address": row.get("duty_addr"),
        "phone": row.get("duty_tel3") or row.get("duty_tel1"),
        # Drawing the map needs coordinates and the route. The engine already has the Tmap route,
        # so it is sent down here too so the client doesn't fetch the same data again.
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "distance_km": evaluation.distance_km,
        "route_distance_km": evaluation.route_distance_km,
        "eta_min": evaluation.eta_min,
        "route": row.get("route"),
        "is_stale": row.get("is_stale"),
        "updated_minutes": round(evaluation.age_minutes) if evaluation.age_minutes is not None else None,
        "tiles": [
            {"key": "eta", "label": "이동 시간", "value": eta_tile, "sub": eta_sub},
            {
                "key": "availability",
                "label": "가용 정보",
                "value": availability_tile,
                "sub": f"{age_text} 갱신",
            },
            {"key": "resource", "label": "필요 자원", "value": resource_tile, "sub": ""},
            {
                "key": "center",
                "label": "응급센터",
                "value": row.get("duty_emcls_name") or "분류 확인 필요",
                "sub": "",
            },
        ],
        "total_score": round(evaluation.total_score),
        "score_breakdown": [
            {
                # Stable key for the screen to identify the item. The label changes to
                # "가용성 대리값" when a proxy is used, so items must not be distinguished by wording.
                "key": comp.key,
                "label": comp.label,
                "score": round(comp.score, 1),
                "max_score": comp.max_score,
                "display": f"{round(comp.score)}/{round(comp.max_score)}",
                "ratio": round(comp.ratio, 3),
                "formula": comp.formula,
            }
            for comp in evaluation.components.values()
        ],
        "reasons": [r.to_dict() for r in build_reasons(evaluation)],
        "summary": build_summary(evaluation),
        "warnings": evaluation.warnings,
        "disclaimer": "표시된 병상 정보는 현재 데이터 기준이며 수용 확정을 의미하지 않습니다.",
    }


# ---------------------------------------------------------------------------
# Evidence detail (the core of the report)
# ---------------------------------------------------------------------------


def build_evidence_detail(evaluation: CandidateEvaluation) -> dict[str, Any]:
    """Lay out in full how every single score was derived and from which values."""
    return {
        "hpid": evaluation.hpid,
        "hospital_name": evaluation.duty_name,
        "grade": evaluation.grade.value,
        "total_score": round(evaluation.total_score, 2),
        "profile": evaluation.profile_key,
        "filters": [f.to_dict() for f in evaluation.filters],
        "components": {key: comp.to_dict() for key, comp in evaluation.components.items()},
        "resource_readings": [
            {
                "column": reading.spec.column,
                "canonical": reading.spec.canonical,
                "label": reading.spec.label,
                "kind": reading.spec.kind.value,
                "raw_value": reading.raw_value,
                "source_field": reading.source_field,
                "state": reading.state.value,
                "freshness": reading.freshness.value,
                "value_text": reading.value_text(),
                "age_minutes": (
                    round(reading.age_minutes, 1) if reading.age_minutes is not None else None
                ),
            }
            for reading in evaluation.readings.values()
        ],
        "warnings": evaluation.warnings,
    }


def render_text_report(evaluation: CandidateEvaluation) -> str:
    """Text report for the terminal / presentations."""
    lines: list[str] = []
    badge = "★ Best" if evaluation.is_best else ""
    lines.append("=" * 78)
    lines.append(
        f"[{evaluation.rank or '-'}] {evaluation.duty_name} ({evaluation.hpid}) "
        f"{badge}  등급 {evaluation.grade.value}"
    )
    lines.append(f"    종합 {evaluation.total_score:.0f}점 / 100점")
    lines.append("=" * 78)

    lines.append("")
    lines.append("■ 점수 근거")
    for comp in evaluation.components.values():
        bar_len = int(round(comp.ratio * 20))
        bar = "█" * bar_len + "·" * (20 - bar_len)
        lines.append(f"  {comp.label:<14} {bar} {comp.score:>5.1f}/{comp.max_score:.0f}")
        lines.append(f"      수식: {comp.formula}")
        for note in comp.notes:
            lines.append(f"      메모: {note}")
        for ev in comp.evidences:
            lines.append(f"      · [{ev.code}] {ev.label} — {ev.detail}")
        lines.append("")

    lines.append("■ 필터 판정 (설계서 §5.2)")
    for outcome in evaluation.filters:
        mark = {
            "PASS": "○",
            "EXCLUDE": "✕",
            "DOWNGRADE": "△",
            "PENALIZE": "-",
            "DEPRIORITIZE": "▼",
            "WARN": "!",
        }.get(outcome.action, "?")
        lines.append(f"  {mark} {outcome.rule} {outcome.label}")
        lines.append(f"      {outcome.detail}")

    lines.append("")
    lines.append("■ 추천 이유 (최대 3개)")
    for reason in build_reasons(evaluation):
        lines.append(f"  · {reason.text}   [{reason.code}]")

    lines.append("")
    lines.append("■ 종합 의견")
    lines.append(f"  {build_summary(evaluation)}")

    if evaluation.warnings:
        lines.append("")
        lines.append("■ 경고")
        for warning in evaluation.warnings:
            lines.append(f"  ! {warning}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# API response
# ---------------------------------------------------------------------------


COMPONENT_LABELS: dict[str, str] = {
    "clinical_fit": "임상·자원 적합성",
    "availability": "가용성 대리값",
    "eta_traffic": "이동 시간",
    "center_capability": "응급센터 역량",
    "freshness": "데이터 최신성",
    "route_weather_stability": "경로·기상 안정성",
}


def build_pipeline(
    result: RecommendationResult,
    *,
    structured: StructuredInput | None = None,
    routed_count: int | None = None,
) -> list[dict[str, Any]]:
    """Return the 6 stages from slide 21 along with the actual numbers.

    The screen must be able to show "in what order things were filtered", so that when
    results are sparse the user reads it as a filter result rather than a malfunction.
    """
    config = result.config
    excluded = len(result.excluded)
    a_grade = sum(1 for c in result.candidates if c.grade is Grade.A)

    steps = result.radius_steps or (
        [{"radius_km": result.radius_km, "rows": result.evaluated_count}]
        if result.radius_km
        else []
    )
    radius_detail = " → ".join(
        f"{s['radius_km']:.0f}km {s['rows']}곳" for s in steps if s.get("radius_km")
    )

    return [
        {
            "step": 1,
            "key": "structure_input",
            "label": "입력 구조화",
            "detail": (
                f"증상 → {result.profile.label} / {MODE_LABELS[config.mode]}"
                + (f" (판정 {structured.source})" if structured else "")
            ),
            "value": result.profile.key,
        },
        {
            "step": 2,
            "key": "prefilter",
            "label": "1차 후보 선정 (직선거리)",
            "detail": radius_detail or "반경 정보 없음",
            "value": result.evaluated_count,
        },
        {
            "step": 3,
            "key": "eligibility",
            "label": "적합성 필터",
            "detail": f"필수 장비 미보유 {excluded}곳 제외",
            "value": result.evaluated_count - excluded,
        },
        {
            "step": 4,
            "key": "routing",
            "label": "TMAP 경로 계산",
            "detail": (
                f"필터를 통과한 후보 중 {routed_count}곳에 실제 거리·ETA·교통을 붙였다"
                if routed_count is not None
                else "경로 미조회 (직선거리 기준)"
            ),
            "value": routed_count,
        },
        {
            "step": 5,
            "key": "scoring",
            "label": "점수 계산",
            "detail": " · ".join(
                f"{COMPONENT_LABELS.get(k, k)} {v:.0f}%" for k, v in config.weights.items() if v
            ),
            "value": config.mode.value,
        },
        {
            "step": 6,
            "key": "select",
            "label": "Top 3 · Best 선정",
            "detail": (
                f"A등급 {a_grade}곳 중 1곳을 Best 로 제시"
                if a_grade
                else "A등급 후보가 없어 Best 를 표시하지 않는다"
            ),
            "value": len(result.candidates),
        },
    ]


def to_api_payload(
    result: RecommendationResult,
    *,
    include_detail: bool = True,
    structured: StructuredInput | None = None,
    routed_count: int | None = None,
) -> dict[str, Any]:
    """Dict for the FastAPI response, shaped to satisfy the design doc §5.6 display policy."""
    return {
        "generated_at": result.generated_at.isoformat(),
        "rule_version": result.config.version_label,
        # Slide 22 - lets the screen show which weights the ranking was computed with.
        "mode": {
            "key": result.mode.value,
            "label": MODE_LABELS[result.mode],
            "severe": result.mode is ScoringMode.SEVERE,
            "weights": [
                {"key": key, "label": COMPONENT_LABELS.get(key, key), "weight": value}
                for key, value in result.config.weights.items()
            ],
            "resource_weight": result.config.resource_weight,
            "disclaimer": (
                "여기서의 중증 신호는 의료진이 내리는 KTAS 확정 분류가 아니라, "
                "검색 결과의 우선순위를 바꾸는 본 서비스의 안전 정책입니다."
            ),
        },
        "input": structured.to_dict() if structured else None,
        "pipeline": build_pipeline(result, structured=structured, routed_count=routed_count),
        "radius_steps": result.radius_steps,
        "best_hpid": result.best.hpid if result.best else None,
        "has_best": result.best is not None,
        "profile": {
            "key": result.profile.key,
            "label": result.profile.label,
            "required": [r.column for r in result.profile.required()],
            "optional": [r.column for r in result.profile.optional()],
            "needs_surgery": result.profile.needs_surgery,
            "unsupported_equipment": list(result.profile.unsupported_equipment),
        },
        "radius_km": result.radius_km,
        # For the on-screen weather display. Same shape as /api/v1/weather so the frontend adapter can be reused.
        # These are exactly the observations used to score route & weather stability, so the display and score match.
        # forecast (for the sky-condition label) isn't used in scoring, so the route fills it in.
        "weather": {"current": result.weather, "forecast": []},
        "evaluated_count": result.evaluated_count,
        "excluded_count": len(result.excluded),
        "notes": result.notes,
        "recommendations": [
            {
                **build_card(candidate),
                **({"evidence": build_evidence_detail(candidate)} if include_detail else {}),
            }
            for candidate in result.candidates
        ],
        "excluded": [
            {
                "hpid": candidate.hpid,
                "hospital_name": candidate.duty_name,
                "rules": [f.rule for f in candidate.filters if f.excluded],
                "reason": next((f.detail for f in candidate.filters if f.excluded), ""),
            }
            for candidate in result.excluded
        ],
        "resource_holders": _resource_holder_notices(result),
        "disclaimer": (
            "공공 API의 가용병상은 실제 환자 수용 확정이 아니며, 본 서비스는 "
            "의료진의 진단·처방을 대체하지 않습니다."
        ),
    }


def _resource_holder_notices(result: RecommendationResult) -> list[dict[str, Any]]:
    """Separately point out institutions that actually reported the required resource but didn't make the top 3.

    For something like burns, there are cases where only one hospital in Seoul reports
    the resource, but its updates have been frozen for years and it falls to STALE.
    It's correct for it to lose on score (old numbers can't be trusted), but erasing
    "the only institution with that resource" from the screen entirely takes an option
    away from the patient. The ranking is left untouched; it is shown only as a separate notice.
    """
    shown = {c.hpid for c in result.candidates}
    required = [
        r.column for r in result.profile.requirements if r.necessity is Necessity.REQUIRED
    ]
    targets = [c for c in required if c not in {"hvec", "hvs01"}]
    if not targets:
        return []

    # Only notify for candidates pushed out of the ranking due to update delay (STALE).
    # Including institutions that are simply outside the top 3 on score (for common resources like
    # operating rooms, almost every hospital has one) would grow the notice to dozens of lines and bury what matters.
    notices: list[dict[str, Any]] = []
    for evaluation in result.evaluated:
        if evaluation.hpid in shown:
            continue
        for column in targets:
            reading = evaluation.readings.get(column)
            if not reading or not reading.count or reading.state is not DataState.STALE:
                continue
            notices.append(
                {
                    "hpid": evaluation.hpid,
                    "hospital_name": evaluation.duty_name,
                    "resource": reading.spec.label,
                    # value_text() returns '갱신 지연' when STALE, but the notice needs to show
                    # the actual recorded number, not the state, to be useful for decisions.
                    "value": f"{reading.count}개" if reading.count is not None else reading.value_text(),
                    "state": reading.state.value,
                    "age_minutes": round(evaluation.age_minutes) if evaluation.age_minutes else None,
                    "distance_km": evaluation.distance_km,
                    "reason": "갱신이 오래돼 순위에는 반영하지 않았습니다. 이동 전 전화 확인이 필요합니다.",
                }
            )
            break
    return notices[:3]


# ---------------------------------------------------------------------------
# Audit log (design doc SEC-006 / §8.2)
# ---------------------------------------------------------------------------


def _bucket_location(latitude: float | None, longitude: float | None, *, digits: int = 2) -> str:
    """SEC-003 - log a grid cell instead of precise coordinates (2 decimal places ≈ 1.1 km grid)."""
    if latitude is None or longitude is None:
        return "unknown"
    return f"{round(latitude, digits)},{round(longitude, digits)}"


def _accuracy_bucket(accuracy_m: float | None) -> str:
    if accuracy_m is None:
        return "unknown"
    for limit, name in ((50, "high"), (200, "medium"), (1000, "low")):
        if accuracy_m <= limit:
            return name
    return "very_low"


def build_audit_log(
    result: RecommendationResult,
    *,
    session_id: str,
    member_type: str = "guest",
    user_latitude: float | None = None,
    user_longitude: float | None = None,
    location_accuracy_m: float | None = None,
    prompt_version: str | None = None,
    model_version: str | None = None,
) -> dict[str, Any]:
    """Design doc §8.2 minimum operational log fields - record only enough to reproduce the recommendation.

    Raw symptom text and precise coordinates are not included. session_id is also stored hashed.
    """
    return {
        "session_hash": hashlib.sha256(session_id.encode()).hexdigest()[:16],
        "member_type": member_type,
        "timestamp": result.generated_at.isoformat(),
        "location_bucket": _bucket_location(user_latitude, user_longitude),
        "location_accuracy_bucket": _accuracy_bucket(location_accuracy_m),
        "rule_version": result.config.rule_version,
        "prompt_version": prompt_version,
        "model_version": model_version,
        "profile_key": result.profile.key,
        "required_columns": [r.column for r in result.profile.required()],
        "radius_km": result.radius_km,
        "evaluated_count": result.evaluated_count,
        "weights": {
            "clinical_fit": result.config.w_clinical,
            "availability": result.config.w_availability,
            "eta_traffic": result.config.w_eta,
            "freshness": result.config.w_freshness,
            "route_weather_stability": result.config.w_stability,
        },
        "candidates": [
            {
                "hospital_id": candidate.hpid,
                "rank": candidate.rank,
                "grade": candidate.grade.value,
                "total_score": round(candidate.total_score, 2),
                "score_components": {
                    key: round(comp.score, 2) for key, comp in candidate.components.items()
                },
                "data_updated_at": (
                    candidate.components["freshness"].inputs.get("observed_at")
                ),
                "data_age_minutes": (
                    round(candidate.age_minutes, 1) if candidate.age_minutes is not None else None
                ),
                "eta_min": candidate.eta_min,
                "reason_codes": [r.code for r in build_reasons(candidate)],
                "filter_actions": [
                    {"rule": f.rule, "action": f.action} for f in candidate.filters
                ],
            }
            for candidate in result.candidates
        ],
        "excluded": [
            {
                "hospital_id": candidate.hpid,
                "rules": [f.rule for f in candidate.filters if f.excluded],
            }
            for candidate in result.excluded
        ],
    }


# ===========================================================================
# 5. Usage example - reproducing the attached screen (Soonchunhyang, 66 points)
# ===========================================================================

_DEMO_NOW = datetime(2026, 8, 9, 14, 30, tzinfo=KST)


def _minutes_ago(minutes: int) -> datetime:
    return _DEMO_NOW - timedelta(minutes=minutes)


# Shape of the hospitals LEFT JOIN bed_status_latest result (find_nearby return value)
_DEMO_CANDIDATES: list[dict[str, Any]] = [
    {
        # The hospital shown on screen. Burn ICU is null, so Grade B + clinical 20/35.
        "hpid": "A1100003",
        "duty_name": "순천향대학교부속서울병원",
        "duty_addr": "서울특별시 용산구 대사관로 59 (한남동)",
        "duty_tel3": "02-709-9117",
        "duty_emcls_name": "지역응급의료센터",
        "latitude": 37.5340,
        "longitude": 127.0016,
        "distance_km": 5.1,
        "hvidate": _minutes_ago(6),
        "hvec": 12,
        "hvs01": 12,
        "hvoc": 2,
        "hv8": None,  # Burn ICU - no information
        "hvctayn": True,
        "hvmriayn": True,
        "route": {"distance_km": 6.6, "duration_min": 18.0, "duration_s": 1080},
    },
    {
        # Burn specialist. Required resource confirmed and fastest → Grade A Best.
        "hpid": "A1100011",
        "duty_name": "한강성심병원",
        "duty_addr": "서울특별시 영"
        "등포구 버드나루로7길 12",
        "duty_tel3": "02-2639-5119",
        "duty_emcls_name": "전문응급의료센터",
        "latitude": 37.5245,
        "longitude": 126.9018,
        "distance_km": 4.2,
        "hvidate": _minutes_ago(4),
        "hvec": 5,
        "hvs01": 20,
        "hvoc": 3,
        "hv8": 3,  # Burn ICU: 3
        "hvctayn": True,
        "route": {
            "distance_km": 4.8,
            "duration_min": 5.8,
            "duration_s": 348,
            "traffic_sections": [{"road_name": "노들로", "speed_kmh": 42.0}],
        },
    },
    {
        # Burn ICU 0 + fresh data → excluded by F-03.
        "hpid": "A1100022",
        "duty_name": "서울백병원",
        "duty_addr": "서울특별시 중구 마른내로 9",
        "duty_emcls_name": "지역응급의료기관",
        "latitude": 37.5638,
        "longitude": 126.9847,
        "distance_km": 3.4,
        "hvidate": _minutes_ago(9),
        "hvec": 4,
        "hvs01": 10,
        "hv8": 0,
        "route": {"distance_km": 3.9, "duration_min": 11.0, "duration_s": 660},
    },
    {
        # Has a value but the data is 92 minutes old → downgraded to Grade B by F-06.
        "hpid": "A1100034",
        "duty_name": "강북삼성병원",
        "duty_addr": "서울특별시 종로구 새문안로 29",
        "duty_emcls_name": "지역응급의료센터",
        "latitude": 37.5688,
        "longitude": 126.9686,
        "distance_km": 4.9,
        "hvidate": _minutes_ago(92),
        "hvec": 8,
        "hvs01": 15,
        "hv8": 2,
        "route": {"distance_km": 5.6, "duration_min": 14.0, "duration_s": 840},
    },
]

# Shape of the weather.get_current_by_latlon result
_DEMO_WEATHER = {"pty": 1, "rn1": 2.0, "t1h": 27.4, "reh": 78.0}

# Required-resource table provided by the user (resource name / column / points / required·optional / current value / display label)
_USER_TABLE = (
    "내과중환자실\thv2\t20\t필수\t3\t내과중환자실\n"
    "외과중환자실\thv3\t20\t옵션\t6\t외과중환자실\n"
    "외과입원실(정형외과)\thv4\t20\t옵션\t6\t외과입원실(정형외과)\n"
    "신경과입원실\thv5\t20\t필수\t3\t신경과입원실\n"
    "신경외과중환자실\thv6\t20\t필수\t0\t신경외과중환자실\n"
    "약물중환자\thv7\t20\t필수\t0\t약물중환자\n"
    "화상중환자\thv8\t20\t필수\t0\t화상중환자\n"
    "외상중환자\thv9\t20\t필수\t0\t외상중환자\n"
    "VENTI(소아)\thv10\t20\t필수\tY\tVENTI(소아)\n"
    "인큐베이터(보육기)\thv11\t20\t필수\tY\t인큐베이터(보육기)\n"
)


def run_demo() -> None:
    """Print recommendation results and evidence for a severe burn scenario."""
    import json

    routes = {row["hpid"]: row["route"] for row in _DEMO_CANDIDATES}

    # Stage 1 - turn the symptom text into conditions.
    structured = structure_input("끓는 물에 팔을 심하게 데였고 의식은 또렷합니다")
    config = ScoringConfig.for_mode(structured.mode)

    result = recommend(
        _DEMO_CANDIDATES,
        profile=structured.profile,
        now=_DEMO_NOW,
        weather=_DEMO_WEATHER,
        routes=routes,
        radius_km=10.0,
        config=config,
    )

    print("\n" + "#" * 78)
    print(f"# 중증 화상 - 추천 결과 ({MODE_LABELS[config.mode]})")
    print("#" * 78)
    for step in build_pipeline(result, structured=structured, routed_count=len(routes)):
        print(f"  {step['step']}. {step['label']}: {step['detail']}")
    print()
    for candidate in result.candidates:
        print(render_text_report(candidate))
        print()

    print("■ 제외된 후보")
    for candidate in result.excluded:
        rules = ", ".join(f.rule for f in candidate.filters if f.excluded)
        detail = next(f.detail for f in candidate.filters if f.excluded)
        print(f"  ✕ {candidate.duty_name} [{rules}] {detail}")

    print("\n■ 운영 메모")
    for note in result.notes:
        print(f"  - {note}")

    # Build a profile directly from the table and evaluate it
    custom_profile = profile_from_table(_USER_TABLE, key="custom", label="표에서 만든 요구자원")
    custom_row = {
        "hpid": "A9999999",
        "duty_name": "표 예시 병원",
        "duty_emcls_name": "권역응급의료센터",
        "latitude": 37.5,
        "longitude": 127.0,
        "distance_km": 2.0,
        "hvidate": _minutes_ago(8),
        "hvec": 6,
        "hvs01": 20,
        # Current values from the table's 5th column (hv10/hv11 stay as the "Y" string)
        **sample_row_from_table(_USER_TABLE),
    }
    table_result = recommend(
        [custom_row], profile=custom_profile, now=_DEMO_NOW, weather=_DEMO_WEATHER, radius_km=5.0
    )
    print("\n" + "#" * 78)
    print("# 표에서 만든 요구자원 프로파일")
    print("#" * 78)
    print(f"필수: {[r.column for r in custom_profile.required()]}")
    print(f"옵션: {[r.column for r in custom_profile.optional()]}")
    print(render_text_report((table_result.candidates + table_result.excluded)[0]))

    # API response / audit log
    payload = to_api_payload(result, include_detail=True)
    audit = build_audit_log(
        result,
        session_id="anon-session-abc123",
        member_type="guest",
        user_latitude=37.5512,
        user_longitude=126.9882,
        location_accuracy_m=35.0,
        prompt_version="triage-v2.0",
        model_version="rule-only",
    )

    first = payload["recommendations"][0]
    print("\n" + "#" * 78)
    print("# API 응답 (첫 후보 발췌)")
    print("#" * 78)
    print(
        json.dumps(
            {
                "hospital_name": first["hospital_name"],
                "total_score": first["total_score"],
                "grade": first["grade"],
                "reasons": first["reasons"],
                "summary": first["summary"],
                "score_breakdown": first["score_breakdown"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    print("\n" + "#" * 78)
    print("# 감사로그 (설계서 §8.2 / SEC-003 / SEC-006)")
    print("#" * 78)
    print(json.dumps(audit, ensure_ascii=False, indent=2))


# ===========================================================================
# 6. Self-check - without pytest: python recommendation_engine.py --test
# ===========================================================================
#
# Verifies that the design doc's safety rules are actually upheld in code. In particular,
# "never mistake no information (null) for no availability (0)" (F-05) and
# "never assert admission is confirmed" (§5.6) lead to safety issues if broken,
# so they are bundled together as regression checks.


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "hpid": "A0000001",
        "duty_name": "테스트병원",
        "duty_emcls_name": "지역응급의료센터",
        "latitude": 37.5,
        "longitude": 127.0,
        "distance_km": 3.0,
        "hvidate": _DEMO_NOW - timedelta(minutes=5),
        "hvec": 10,
        "hvs01": 20,
        "hvoc": 2,
    }
    row.update(overrides)
    return row


def _close(actual: float, expected: float, tol: float = 1e-6) -> bool:
    return abs(actual - expected) <= tol


def run_self_test() -> int:
    """Verify the design doc safety rules. Returns the number of failures."""
    checks: list[tuple[str, Callable[[], None]]] = []

    def check(name: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
        def wrap(fn: Callable[[], None]) -> Callable[[], None]:
            checks.append((name, fn))
            return fn

        return wrap

    burn = get_profile("burn")

    # --- F-05 / DATA-006: distinguish null from 0 ----------------------------
    @check("F-05 필수 병상 null 은 제외가 아니라 B등급")
    def _() -> None:
        ev = evaluate_candidate(_row(hv8=None), profile=burn, now=_DEMO_NOW)
        assert ev.grade is Grade.B
        assert not ev.excluded
        assert "화상중환자" in ev.unconfirmed
        assert any(f.rule == "F-05" for f in ev.filters)

    @check("필수 병상 0 은 제외가 아니라 경미한 감점 (슬라이드 22)")
    def _() -> None:
        empty = evaluate_candidate(_row(hv8=0), profile=burn, now=_DEMO_NOW)
        assert not empty.excluded
        assert empty.deprioritized is False
        assert any(f.rule == "F-03" and f.action == "PENALIZE" for f in empty.filters)
        assert empty.bed_signal["zero_beds"] == ["화상중환자"]
        # The penalty must be "minor". Only availability drops; the fit band is kept.
        assert empty.components["clinical_fit"].score == DEFAULT_CONFIG.w_clinical
        assert empty.components["availability"].score < DEFAULT_CONFIG.w_availability

    @check("필수 장비 미보유는 거리와 무관하게 제외 (슬라이드 21)")
    def _() -> None:
        profile = profile_from_table("인공호흡기(성인)\thvventiayn\t20\t필수\tN\t인공호흡기(성인)")
        ev = evaluate_candidate(_row(hvventiayn="N"), profile=profile, now=_DEMO_NOW)
        assert ev.grade is Grade.EXCLUDED
        assert any(f.rule == "F-03" and f.excluded for f in ev.filters)

    @check("F-06 갱신 지연은 제외가 아니라 B등급 강등")
    def _() -> None:
        ev = evaluate_candidate(
            _row(hv8=3, hvidate=_DEMO_NOW - timedelta(minutes=95)), profile=burn, now=_DEMO_NOW
        )
        assert ev.grade is Grade.B
        assert ev.readings["hv8"].state is DataState.STALE
        assert ev.components["freshness"].score == 0

    @check("음수 병상은 대기 인원으로 읽고 후보에 남긴다 (슬라이드 22)")
    def _() -> None:
        ev = evaluate_candidate(_row(hv8=-3), profile=burn, now=_DEMO_NOW)
        assert ev.readings["hv8"].state is DataState.QUEUED
        assert ev.readings["hv8"].queue_depth == 3
        assert ev.readings["hv8"].value_text() == "대기 3명 추정"
        assert not ev.excluded and not ev.deprioritized
        assert ev.bed_signal["queue_depth"] == 3

    @check("0 > -1 > -5 순으로 단계적 감점")
    def _() -> None:
        scores = [
            evaluate_candidate(_row(hv8=value), profile=burn, now=_DEMO_NOW)
            .components["availability"]
            .score
            for value in (0, -1, -5)
        ]
        assert scores[0] > scores[1] > scores[2], scores

    @check("-6 이하는 제외가 아니라 후순위")
    def _() -> None:
        ev = evaluate_candidate(_row(hv8=-8), profile=burn, now=_DEMO_NOW)
        assert not ev.excluded
        assert ev.deprioritized is True
        assert any(f.rule == "F-03" and f.action == "DEPRIORITIZE" for f in ev.filters)
        # Even with a higher score, a deprioritized hospital goes to the back.
        rows = [
            _row(hpid="BACKLOG", hv8=-8, route={"duration_min": 5.0}),
            _row(hpid="OK", hv8=1, route={"duration_min": 30.0}),
        ]
        result = recommend(rows, profile=burn, now=_DEMO_NOW)
        assert [c.hpid for c in result.candidates] == ["OK", "BACKLOG"]

    @check("중증 모드는 대기 과다여도 후보를 유지한다 (슬라이드 22)")
    def _() -> None:
        ev = evaluate_candidate(
            _row(hv8=-8), profile=burn, now=_DEMO_NOW, config=SEVERE_CONFIG
        )
        assert ev.deprioritized is False
        assert any(f.rule == "F-03" and f.action == "PENALIZE" for f in ev.filters)

    @check("추천 응답은 채점에 쓴 실황을 그대로 싣는다")
    def _() -> None:
        storm = {"pty": 3, "rn1": 30.0, "t1h": -2.0}
        res = recommend([_row(hv8=3)], profile=burn, now=_DEMO_NOW, weather=storm)
        payload = to_api_payload(res, include_detail=False)
        assert payload["weather"]["current"] == storm
        # With heavy rain, route & weather stability must be 0 (display and score see the same value).
        stability = next(
            s for s in payload["recommendations"][0]["score_breakdown"]
            if s["key"] == "route_weather_stability"
        )
        assert stability["score"] == 0
        # Without weather, current is None and the screen omits the display.
        dry = to_api_payload(recommend([_row(hv8=3)], profile=burn, now=_DEMO_NOW))
        assert dry["weather"]["current"] is None

    @check("자릿수 오입력(12312)은 가용이 아니라 정보 없음")
    def _() -> None:
        assert _coerce_count(12312, ResourceKind.COUNT) == (None, DataState.UNKNOWN)
        assert _coerce_count(MAX_PLAUSIBLE_COUNT, ResourceKind.COUNT) == (
            MAX_PLAUSIBLE_COUNT,
            DataState.AVAILABLE,
        )
        ev = evaluate_candidate(_row(hv8=12312), profile=burn, now=_DEMO_NOW)
        assert ev.readings["hv8"].state is DataState.UNKNOWN

    # --- hv10 / hv11: Y/N flag resources -------------------------------------
    @check("hv10/hv11 은 Y/N 문자열로 읽는다")
    def _() -> None:
        profile = profile_from_table("VENTI(소아)\thv10\t20\t필수\tY\tVENTI(소아)")
        ev = evaluate_candidate(_row(hv10="Y"), profile=profile, now=_DEMO_NOW)
        assert ev.readings["hv10"].state is DataState.AVAILABLE
        assert ev.readings["hv10"].value_text() == "가용"
        assert ev.grade is Grade.A

    @check("플래그 자원 N 은 필수 불일치로 제외")
    def _() -> None:
        profile = profile_from_table("인큐베이터(보육기)\thv11\t20\t필수\tN\t인큐베이터(보육기)")
        ev = evaluate_candidate(_row(hv11="N"), profile=profile, now=_DEMO_NOW)
        assert ev.grade is Grade.EXCLUDED

    @check("컬럼이 비면 raw JSONB 원본을 폴백으로 읽는다")
    def _() -> None:
        profile = profile_from_table("VENTI(소아)\thv10\t20\t필수\tY\tVENTI(소아)")
        ev = evaluate_candidate(_row(hv10=None, raw={"hv10": "Y"}), profile=profile, now=_DEMO_NOW)
        assert ev.readings["hv10"].state is DataState.AVAILABLE
        assert ev.readings["hv10"].source_field == "raw.hv10"

    # --- Score formulas (design doc §5.3.1) ----------------------------------
    @check("log 정규화는 병상 5개에서 포화한다")
    def _() -> None:
        assert _close(log_scale(5), 1.0)
        assert _close(log_scale(50), 1.0)
        assert log_scale(0) == 0.0
        assert log_scale(3) < 1.0

    @check("임상 적합성 구간 = 만점 / 55% / 0")
    def _() -> None:
        full = evaluate_candidate(_row(hv8=3), profile=burn, now=_DEMO_NOW)
        partial = evaluate_candidate(_row(hv8=None), profile=burn, now=_DEMO_NOW)
        w = DEFAULT_CONFIG.w_clinical
        assert _close(full.components["clinical_fit"].score, w)
        assert _close(partial.components["clinical_fit"].score, w * 0.55)
        # Mismatch (0 points) applies only to equipment 'N'. Zero beds don't count here.
        flag_profile = profile_from_table("CT\thvctayn\t20\t필수\tN\tCT")
        mismatch = evaluate_candidate(_row(hvctayn="N"), profile=flag_profile, now=_DEMO_NOW)
        assert mismatch.components["clinical_fit"].score == 0

    @check("ETA 는 최소 ETA 대비 상대 점수")
    def _() -> None:
        rows = [
            _row(hpid="FAST", hv8=1, route={"duration_min": 6.0, "distance_km": 4.0}),
            _row(hpid="SLOW", hv8=1, route={"duration_min": 18.0, "distance_km": 12.0}),
        ]
        result = recommend(rows, profile=burn, now=_DEMO_NOW)
        by_id = {c.hpid: c for c in result.candidates}
        w = DEFAULT_CONFIG.w_eta
        assert _close(by_id["FAST"].components["eta_traffic"].score, w)
        assert _close(by_id["SLOW"].components["eta_traffic"].score, w * 6 / 18, 1e-9)

    @check("최신성 구간 비율 100 / 70 / 30 / 0%")
    def _() -> None:
        w = DEFAULT_CONFIG.w_freshness
        for minutes, ratio in ((5, 1.0), (20, 0.7), (45, 0.3), (120, 0.0)):
            ev = evaluate_candidate(
                _row(hv8=3, hvidate=_DEMO_NOW - timedelta(minutes=minutes)),
                profile=burn,
                now=_DEMO_NOW,
            )
            assert _close(ev.components["freshness"].score, w * ratio, 1e-9), minutes

    @check("경로 실패는 0점 + 예상시간 미제공 경고")
    def _() -> None:
        ev = evaluate_candidate(_row(hv8=3), profile=burn, now=_DEMO_NOW)
        assert ev.components["eta_traffic"].score == 0
        assert ev.eta_min is None
        assert any("소요시간" in w for w in ev.warnings)

    @check("두 모드 모두 가중치 합계는 100")
    def _() -> None:
        for mode in ScoringMode:
            config = ScoringConfig.for_mode(mode)
            assert _close(sum(config.weights.values()), 100.0), mode

    @check("모드 전환이 슬라이드 22 가중치와 일치한다")
    def _() -> None:
        normal = ScoringConfig.for_mode(ScoringMode.NORMAL)
        severe = ScoringConfig.for_mode(ScoringMode.SEVERE)
        # Normal: travel time 50%, distance-centric
        assert normal.w_eta == 50
        # Severe: travel 30 / resources 40 / center capability 20
        assert severe.w_eta == 30
        assert severe.resource_weight == 40
        assert severe.w_center == 20

    @check("중증 모드에서는 센터 역량이 순위를 뒤집는다")
    def _() -> None:
        rows = [
            _row(
                hpid="SMALL_NEAR",
                hv8=2,
                duty_emcls_name="지역응급의료기관",
                route={"duration_min": 8.0},
            ),
            _row(
                hpid="BIG_FAR",
                hv8=2,
                duty_emcls_name="권역응급의료센터",
                route={"duration_min": 10.0},
            ),
        ]
        normal = recommend(rows, profile=burn, now=_DEMO_NOW, config=DEFAULT_CONFIG)
        severe = recommend(rows, profile=burn, now=_DEMO_NOW, config=SEVERE_CONFIG)
        assert normal.candidates[0].hpid == "SMALL_NEAR"
        assert severe.candidates[0].hpid == "BIG_FAR"

    @check("모든 점수 컴포넌트가 수식을 들고 있다")
    def _() -> None:
        ev = evaluate_candidate(
            _row(hv8=3, route={"duration_min": 9.0, "distance_km": 5.0}),
            profile=burn,
            now=_DEMO_NOW,
        )
        for component in ev.components.values():
            assert component.formula, component.key
            assert component.inputs != {} or component.evidences

    # --- Display policy (design doc §5.6) ------------------------------------
    @check("추천 이유는 최대 3개")
    def _() -> None:
        ev = evaluate_candidate(
            _row(hv8=5, route={"duration_min": 7.0, "distance_km": 5.0}),
            profile=burn,
            now=_DEMO_NOW,
        )
        assert len(build_reasons(ev)) <= 3

    @check("stale 병상 숫자는 이유 칩으로 내보내지 않는다")
    def _() -> None:
        ev = evaluate_candidate(
            _row(hv8=2, hvidate=_DEMO_NOW - timedelta(minutes=92), route={"duration_min": 14.0}),
            profile=burn,
            now=_DEMO_NOW,
        )
        assert all(r.code != "RSN-ER" for r in build_reasons(ev))

    @check("대리 산정된 가용성은 이유 칩으로 내보내지 않는다")
    def _() -> None:
        ev = evaluate_candidate(
            _row(hv8=None, route={"duration_min": 12.0}), profile=burn, now=_DEMO_NOW
        )
        assert ev.components["availability"].inputs["fallback_used"] is True
        assert all(r.code != "RSN-ER" for r in build_reasons(ev))
        assert any(r.code == "RSN-BED-UNKNOWN" for r in build_reasons(ev))

    @check("A등급이 없으면 Best 대신 '우선 확인 후보'")
    def _() -> None:
        result = recommend(
            [_row(hpid="B1", hv8=None, route={"duration_min": 8.0})],
            profile=burn,
            now=_DEMO_NOW,
        )
        assert result.candidates[0].grade is Grade.B
        assert result.candidates[0].is_best is False
        assert build_card(result.candidates[0])["badge"] == "우선 확인 후보"

    @check("결과는 최대 3곳")
    def _() -> None:
        rows = [_row(hpid=f"H{i}", hv8=3, route={"duration_min": float(i + 5)}) for i in range(10)]
        assert len(recommend(rows, profile=burn, now=_DEMO_NOW).candidates) == 3

    @check("생성 문장은 수용/진료를 단정하지 않는다")
    def _() -> None:
        for hv8 in (3, None):
            ev = evaluate_candidate(
                _row(hv8=hv8, route={"duration_min": 10.0}), profile=burn, now=_DEMO_NOW
            )
            assert_no_capacity_claim(build_summary(ev))

    @check("수용확정 가드는 단정만 잡고 부정문은 통과시킨다")
    def _() -> None:
        assert_no_capacity_claim("병상 수는 수용 확정이 아닙니다.")
        assert_no_capacity_claim("실제 진료 가능 여부를 확인해 주세요.")
        for bad in ("지금 바로 진료 가능합니다.", "해당 병원 수용 확정 상태입니다."):
            try:
                assert_no_capacity_claim(bad)
            except CapacityClaimError:
                continue
            raise AssertionError(f"가드가 놓쳤다: {bad}")

    # --- Ranking / tie-breaking (design doc §5.3) ----------------------------
    @check("자원 미확인 기관은 더 가까워도 Best 가 되지 않는다")
    def _() -> None:
        # Slide 21 safety measure. Independent of score ranking, Best only comes from Grade A.
        rows = [
            _row(hpid="NEAR_UNKNOWN", hv8=None, distance_km=1.0, route={"duration_min": 5.0}),
            _row(hpid="FAR_CONFIRMED", hv8=4, distance_km=9.0, route={"duration_min": 9.0}),
        ]
        for config in (DEFAULT_CONFIG, SEVERE_CONFIG):
            result = recommend(rows, profile=burn, now=_DEMO_NOW, config=config)
            near = next(c for c in result.candidates if c.hpid == "NEAR_UNKNOWN")
            assert near.grade is Grade.B, config.mode
            assert near.is_best is False, config.mode
            assert result.best is not None and result.best.hpid == "FAR_CONFIRMED", config.mode

    @check("중증 모드는 자원 확인 기관을 순위로도 끌어올린다")
    def _() -> None:
        rows = [
            _row(hpid="NEAR_UNKNOWN", hv8=None, distance_km=1.0, route={"duration_min": 6.0}),
            _row(hpid="FAR_CONFIRMED", hv8=4, distance_km=9.0, route={"duration_min": 8.0}),
        ]
        normal = recommend(rows, profile=burn, now=_DEMO_NOW, config=DEFAULT_CONFIG)
        severe = recommend(rows, profile=burn, now=_DEMO_NOW, config=SEVERE_CONFIG)
        assert normal.candidates[0].hpid == "NEAR_UNKNOWN"
        assert severe.candidates[0].hpid == "FAR_CONFIRMED"

    @check("기상 5점이 임상 적합성을 뒤집지 못한다")
    def _() -> None:
        rows = [
            _row(hpid="CONFIRMED", hv8=2, route={"duration_min": 10.0}),
            _row(hpid="UNKNOWN", hv8=None, route={"duration_min": 10.0}),
        ]
        storm = {"pty": 3, "rn1": 30.0, "t1h": -2.0}
        result = recommend(rows, profile=burn, now=_DEMO_NOW, weather=storm)
        assert result.candidates[0].hpid == "CONFIRMED"

    # --- Table input ---------------------------------------------------------
    @check("표에서 필수/옵션·배점을 읽는다")
    def _() -> None:
        profile = profile_from_table(_USER_TABLE)
        assert [r.column for r in profile.required()] == [
            "hv2", "hv5", "hv6", "hv7", "hv8", "hv9", "hv10", "hv11",
        ]
        assert [r.column for r in profile.optional()] == ["hv3", "hv4"]
        assert all(r.weight == 20 for r in profile.requirements)

    @check("표의 현재값에서 Y/N 문자열이 보존된다")
    def _() -> None:
        row = sample_row_from_table(_USER_TABLE)
        assert row["hv2"] == "3"
        assert row["hv6"] == "0"
        assert row["hv10"] == "Y"

    @check("표의 필수 병상 0 은 제외가 아니라 감점 사유가 된다")
    def _() -> None:
        profile = profile_from_table(_USER_TABLE)
        ev = evaluate_candidate(
            _row(**sample_row_from_table(_USER_TABLE)), profile=profile, now=_DEMO_NOW
        )
        assert not ev.excluded
        assert "신경외과중환자실" in ev.bed_signal["zero_beds"]

    # --- Unsupported equipment (design doc §2.4 / F-04) ----------------------
    @check("데이터 없는 장비로 병원을 걸러 내지 않는다")
    def _() -> None:
        ev = evaluate_candidate(_row(hv7=2), profile=get_profile("poisoning"), now=_DEMO_NOW)
        assert ev.grade is Grade.B
        assert any(f.rule == "F-04" and f.downgraded for f in ev.filters)
        assert "hyperbaric_oxygen_available" in ev.unsupported_equipment
        assert _close(ev.components["clinical_fit"].score, DEFAULT_CONFIG.w_clinical * 0.55)

    # --- Screen reproduction -------------------------------------------------
    @check("데모 후보의 breakdown 이 고정돼 있다 (회귀 감지용)")
    def _() -> None:
        routes = {row["hpid"]: row["route"] for row in _DEMO_CANDIDATES}
        result = recommend(
            _DEMO_CANDIDATES,
            profile=burn,
            now=_DEMO_NOW,
            weather=_DEMO_WEATHER,
            routes=routes,
        )
        target = next(c for c in result.candidates if c.hpid == "A1100003")
        # Values re-pinned against rule-v2.0 (normal mode). Touching the formulas will be caught here.
        assert round(target.total_score) == 53, target.total_score
        expected = {
            "clinical_fit": 11.0,
            "availability": 10.0,
            "eta_traffic": 16.11,
            "center_capability": 7.5,
            "freshness": 7.0,
            "route_weather_stability": 1.8,
        }
        for key, value in expected.items():
            assert _close(target.components[key].score, value, 0.01), (
                key,
                target.components[key].score,
            )

    @check("같은 후보군이 모드에 따라 다른 순서로 나온다")
    def _() -> None:
        routes = {row["hpid"]: row["route"] for row in _DEMO_CANDIDATES}
        kwargs = dict(profile=burn, now=_DEMO_NOW, weather=_DEMO_WEATHER, routes=routes)
        normal = recommend(_DEMO_CANDIDATES, config=DEFAULT_CONFIG, **kwargs)
        severe = recommend(_DEMO_CANDIDATES, config=SEVERE_CONFIG, **kwargs)
        assert [c.hpid for c in normal.candidates] != [c.hpid for c in severe.candidates]
        # In both modes, Best only comes from Grade A.
        for result in (normal, severe):
            assert result.best is None or result.best.grade is Grade.A

    # --- 6-stage pipeline (slide 21) -----------------------------------------
    @check("입력 구조화가 증상 문장을 프로파일·모드로 바꾼다")
    def _() -> None:
        burned = structure_input("끓는 물에 데였어요")
        assert burned.profile_key == "burn"
        # The burn profile defaults to severe.
        assert burned.mode is ScoringMode.SEVERE

        mild = structure_input("배가 조금 아파요")
        assert mild.mode is ScoringMode.NORMAL

        flagged = structure_input("배가 아픈데 의식이 없어요")
        assert flagged.mode is ScoringMode.SEVERE
        assert "의식이 없" in flagged.severity_signals

        # An explicit setting beats automatic detection.
        assert structure_input("화상", severity="normal").mode is ScoringMode.NORMAL

    @check("에이전트가 모르는 프로파일을 주면 키워드 규칙으로 되돌린다")
    def _() -> None:
        out = structure_input("가슴이 조여요", agent_result={"profile": "made_up"})
        assert out.profile_key == "cardiac"
        assert out.source == "rules"
        assert out.note

    @check("반경은 5 → 10 → 20km 로 넓힌다")
    def _() -> None:
        seen: list[float] = []

        def fetch(radius_km: float) -> list[dict[str, Any]]:
            seen.append(radius_km)
            # Only return candidates at 20 km, to check that the expansion actually runs.
            return [_row(hv8=3, route={"duration_min": 9.0})] if radius_km >= 20 else []

        result = recommend_with_radius_expansion(fetch, profile=burn, now=_DEMO_NOW)
        assert seen == [5.0, 10.0, 20.0], seen
        assert result.radius_km == 20.0
        assert [s["radius_km"] for s in result.radius_steps] == [5.0, 10.0, 20.0]

    @check("A등급이 없으면 Best 를 아예 내주지 않는다")
    def _() -> None:
        result = recommend(
            [_row(hpid="B1", hv8=None, route={"duration_min": 8.0})],
            profile=burn,
            now=_DEMO_NOW,
        )
        assert result.candidates and result.best is None
        payload = to_api_payload(result, include_detail=False)
        assert payload["has_best"] is False
        assert payload["best_hpid"] is None

    @check("응답이 6단계 파이프라인과 모드 가중치를 함께 싣는다")
    def _() -> None:
        structured = structure_input("화상을 입었어요")
        result = recommend(
            [_row(hv8=3, route={"duration_min": 9.0})],
            profile=structured.profile,
            now=_DEMO_NOW,
            config=ScoringConfig.for_mode(structured.mode),
            radius_km=5.0,
        )
        payload = to_api_payload(
            result, include_detail=False, structured=structured, routed_count=1
        )
        assert [s["step"] for s in payload["pipeline"]] == [1, 2, 3, 4, 5, 6]
        assert payload["mode"]["key"] == "severe"
        assert _close(sum(w["weight"] for w in payload["mode"]["weights"]), 100.0)
        assert payload["input"]["profile"] == "burn"
        assert "KTAS" in payload["mode"]["disclaimer"]

    failed = 0
    for name, fn in checks:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - the check runner collects and reports every failure
            failed += 1
            print(f"  ✕ {name}\n      {type(exc).__name__}: {exc}")
        else:
            print(f"  ○ {name}")

    print(f"\n{len(checks) - failed}/{len(checks)} 통과")
    return failed


if __name__ == "__main__":
    import sys

    if "--test" in sys.argv:
        raise SystemExit(1 if run_self_test() else 0)
    run_demo()
