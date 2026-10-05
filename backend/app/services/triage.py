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
    settings = get_settings()
    if not settings.project_endpoint:
        raise TriageAgentNotConfigured("PROJECT_ENDPOINT 환경 변수가 비어 있습니다.")
    if not settings.agent_name:
        raise TriageAgentNotConfigured("AGENT_NAME 환경 변수가 비어 있습니다.")

    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
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
    _openai_client.cache_clear()


def is_configured() -> bool:
    settings = get_settings()
    return bool(settings.project_endpoint and settings.agent_name)


def parse_agent_output(raw_output: str) -> dict[str, Any]:
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
        raise TriageAgentError(f"에이전트 호출 실패: {exc}") from exc

    return parse_agent_output(getattr(response, "output_text", ""))
