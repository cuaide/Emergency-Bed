"""Azure AI Foundry 에이전트 호출 (증상 분류).

검색·생성(RAG)은 Foundry에 배포된 에이전트가 수행한다. 이 저장소에는 임베딩이나벡터 인덱스가 없고, 여기서는 호출과 응답 정규화만 담당한다.

speech.py 와 마찬가지로 HTTPException을 던지지 않는다. HTTP 상태 코드 변환 및 예외 처리는 api/routes/triage.py 가 담당한다.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


class TriageAgentError(RuntimeError):
    """에이전트 호출이 실패했거나 응답을 해석하지 못한 경우 (외부 원인)."""


class TriageAgentNotConfigured(TriageAgentError):
    """PROJECT_ENDPOINT 등 설정이 비어 호출 자체가 불가능한 경우 (우리 쪽 원인)."""


@lru_cache(maxsize=1)
def _openai_client():
    """AIProjectClient와 자격 증명은 한 번만 만든다.

    요청마다 DefaultAzureCredential을 새로 만들면 매번 토큰을 다시 받아 느려진다.
    lru_cache는 예외를 캐시하지 않으므로, 설정이 채워지면 다음 호출에서 정상 생성된다.
    """
    settings = get_settings()
    if not settings.project_endpoint:
        raise TriageAgentNotConfigured("PROJECT_ENDPOINT 환경 변수가 비어 있습니다.")
    if not settings.agent_name:
        raise TriageAgentNotConfigured("AGENT_NAME 환경 변수가 비어 있습니다.")

    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:  # pragma: no cover - 설치 여부에 따라 갈림
        raise TriageAgentNotConfigured(
            "azure-ai-projects / azure-identity 패키지가 설치돼 있지 않습니다. "
            "pip install -r requirements.txt 를 실행하세요."
        ) from exc

    project_client = AIProjectClient(
        endpoint=settings.project_endpoint,
        credential=DefaultAzureCredential(),
    )
    return project_client.get_openai_client()


def reset_client_cache() -> None:
    """테스트에서 환경 변수를 바꾼 뒤 호출."""
    _openai_client.cache_clear()


def is_configured() -> bool:
    settings = get_settings()
    return bool(settings.project_endpoint and settings.agent_name)


def parse_agent_output(raw_output: str) -> dict[str, Any]:
    """에이전트 응답 텍스트에서 JSON 객체를 꺼낸다.

    프롬프트로 JSON을 요구해도 ```json 펜스나 앞뒤 설명을 붙여 오는 경우가 있다.
    원문 → 코드펜스 블록 순으로 시도해, 설명이 섞여 있어도 본문을 건진다.
    """
    text = (raw_output or "").strip()
    if not text:
        raise TriageAgentError("에이전트 응답이 비어 있습니다.")

    candidates = [text]
    if "```" in text:
        blocks = text.replace("```json", "```").split("```")
        candidates.extend(block.strip() for block in blocks if block.strip())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    logger.warning("에이전트 응답 JSON 해석 실패: %r", text[:200])
    raise TriageAgentError("에이전트 응답을 JSON으로 해석하지 못했습니다.")


def call_triage_agent(user_query: str) -> dict[str, Any]:
    """증상 문장을 에이전트에 넘겨 분류 결과(JSON)를 받는다.

    수 초가 걸리는 블로킹 호출이다. 호출하는 라우트는 반드시 동기(def)여야 한다.
    """
    query = (user_query or "").strip()
    if not query:
        raise TriageAgentError("증상 내용이 비어 있습니다.")

    settings = get_settings()
    client = _openai_client()

    try:
        response = client.responses.create(
            input=[{"role": "user", "content": f"환자 증상: {query}"}],
            extra_body={
                "agent_reference": {
                    "name": settings.agent_name,
                    "version": settings.agent_version,
                    "type": "agent_reference",
                }
            },
        )
    except Exception as exc:
        # SDK가 던지는 예외 종류가 넓어(인증·네트워크·HTTP) 도메인 예외로 감싼다.
        raise TriageAgentError(f"에이전트 호출 실패: {exc}") from exc

    return parse_agent_output(getattr(response, "output_text", ""))
