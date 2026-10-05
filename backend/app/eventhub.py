"""Event Hub serialization helpers.
 
The output binding in function_app.py only needs a list of strings, so the SDK is not
required there. send_to_event_hub() is also provided for when you want to send
directly from the local CLI.
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
    """Serialize into one JSON string per hospital (avoids the message size limit)."""
    return [json.dumps(row, ensure_ascii=False, default=_default) for row in rows]
 
 
def decode_event_hub_messages(events: Iterable[Any]) -> list[dict[str, Any]]:
    """Decode a list of EventHubEvent objects into a list of dicts.
 
    Even if a single event contains an array (batch send), it is flattened before returning.
    Messages that fail JSON parsing are skipped with only a warning log.
    """
    rows: list[dict[str, Any]] = []
    for event in events:
        try:
            body = event.get_body()
        except AttributeError:  # Already bytes/str
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
    """Send directly to Event Hub from local/CLI (substitute for the Functions output binding)."""
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
            except ValueError:  # Batch is full
                producer.send_batch(batch)
                batch = producer.create_batch()
                batch.add(EventData(message))
        if len(batch) > 0:
            producer.send_batch(batch)
 
    logger.info("Event Hub 전송 완료 %d건 → %s", len(messages), settings.event_hub_name)
    return len(messages)
 