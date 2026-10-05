"""Event Hub 직렬화 헬퍼.

function_app.py의 출력 바인딩은 문자열 리스트만 넘기면 되므로 SDK가 필요 없지만,
로컬 CLI에서 직접 전송해 보고 싶을 때를 위해 send_to_event_hub()도 제공한다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


def _default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def to_event_messages(rows: Sequence[dict[str, Any]]) -> list[str]:
    """병원별 1건씩 JSON 문자열로 직렬화 (메시지 크기 상한 회피)."""
    return [json.dumps(row, ensure_ascii=False, default=_default) for row in rows]


def decode_event_hub_messages(events: Iterable[Any]) -> list[dict[str, Any]]:
    """EventHubEvent 목록을 dict 목록으로 디코딩한다.

    한 이벤트에 배열이 담겨 있어도(배치 전송) 평탄화해서 돌려준다.
    JSON 파싱에 실패한 메시지는 경고 로그만 남기고 건너뛴다.
    """
    rows: list[dict[str, Any]] = []
    for event in events:
        try:
            body = event.get_body()
        except AttributeError:  # 이미 bytes/str인 경우
            body = event
        if isinstance(body, (bytes, bytearray)):
            body = bytes(body).decode("utf-8")

        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            logger.warning("JSON 파싱 실패로 이벤트 1건 폐기: %r", str(body)[:200])
            continue

        if isinstance(payload, list):
            rows.extend(item for item in payload if isinstance(item, dict))
        elif isinstance(payload, dict):
            rows.append(payload)
        else:
            logger.warning("dict/list가 아닌 이벤트 폐기: %s", type(payload).__name__)
    return rows


def send_to_event_hub(rows: Sequence[dict[str, Any]]) -> int:
    """로컬/CLI에서 Event Hub로 직접 전송 (Functions 출력 바인딩 대체용)."""
    from azure.eventhub import EventData, EventHubProducerClient

    settings = get_settings()
    if not settings.event_hub_connection:
        raise RuntimeError("EventHubConnection 환경 변수가 비어 있습니다.")

    messages = to_event_messages(rows)
    if not messages:
        return 0

    producer = EventHubProducerClient.from_connection_string(
        conn_str=settings.event_hub_connection,
        eventhub_name=settings.event_hub_name,
    )
    with producer:
        batch = producer.create_batch()
        for message in messages:
            try:
                batch.add(EventData(message))
            except ValueError:  # 배치가 가득 참
                producer.send_batch(batch)
                batch = producer.create_batch()
                batch.add(EventData(message))
        if len(batch) > 0:
            producer.send_batch(batch)

    logger.info("Event Hub 전송 완료 %d건 → %s", len(messages), settings.event_hub_name)
    return len(messages)
