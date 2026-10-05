import logging
import os
from typing import List
import azure.functions as func

from app.collectors.emergency import (
    fetch_bed_status,
    fetch_hospitals,
    save_bed_status,
    save_hospitals
)
from app.collectors.weather import (
    NoTargetGridError,
    backfill_hospital_grids,
    fetch_weather_current,
    fetch_weather_forecast,
    save_weather_current,
    save_weather_forecast
)
from app.eventhub import decode_event_hub_messages, to_event_messages

app = func.FunctionApp()

BED_COLLECT_SCHEDULE = os.environ.get("BED_COLLECT_SCHEDULE", "0 */10 * * * *")
HOSPITAL_COLLECT_SCHEDULE = os.environ.get("HOSPITAL_COLLECT_SCHEDULE", "0 30 18 * * *")
WEATHER_CURRENT_SCHEDULE = os.environ.get("WEATHER_CURRENT_SCHEDULE", "0 45 * * * *")
WEATHER_FORECAST_SCHEDULE = os.environ.get(
    "WEATHER_FORECAST_SCHEDULE", "0 20 2,5,8,11,14,17,20,23 * * *"
)

@app.timer_trigger(
    schedule=BED_COLLECT_SCHEDULE,
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True
)
@app.event_hub_output(
    arg_name="output_events",
    event_hub_name="%BED_EVENT_HUB_NAME%",
    connection="EventHubConnection"
)
def collect_beds_timer(
    timer: func.TimerRequest,
    output_events: func.Out[List[str]]) -> None:
    """공공 API에서 실시간 병상을 수집해 Event Hub로 흘려보낸다 (DB 적재 없음)."""
    if timer.past_due:
        logging.warning("collect_beds_timer 실행이 지연되었습니다.")

    rows = fetch_bed_status()
    if not rows:
        logging.warning("수집된 병상 정보가 없어 Event Hub 전송을 건너뜁니다.")
        return

    messages = to_event_messages(rows)
    output_events.set(messages)
    logging.info("병상 이벤트 %d건 전송 완료", len(messages))

@app.event_hub_message_trigger(
    arg_name="events",
    event_hub_name="%BED_EVENT_HUB_NAME%",
    connection="EventHubConnection",
    consumer_group="%BED_EVENT_HUB_CONSUMER_GROUP%",
    cardinality="many",
)

def save_beds_eventhub(events: List[func.EventHubEvent]) -> None:
    rows = decode_event_hub_messages(events)
    if not rows:
        logging.warning("수신 이벤트 %d건 중 적재 가능한 메시지가 없습니다.", len(events))
        return

    inserted_count = save_bed_status(rows)
    logging.info(
        "병상 이벤트 수신=%d, 파싱=%d, DB 적재=%d",
        len(events),
        len(rows),
        inserted_count,
    )


@app.timer_trigger(
    schedule=HOSPITAL_COLLECT_SCHEDULE,
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def collect_hospitals_daily(timer: func.TimerRequest) -> None:
    rows = fetch_hospitals()
    saved = save_hospitals(rows)
    filled = backfill_hospital_grids()
    logging.info("기관 기본정보 수집=%d, DB 적재=%d, 격자 보정=%d", len(rows), saved, filled)


@app.timer_trigger(
    schedule=WEATHER_CURRENT_SCHEDULE,
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def collect_weather_current_timer(timer: func.TimerRequest) -> None:
    
    if timer.past_due:
        logging.warning("collect_weather_current_timer 실행이 지연되었습니다.")

    try:
        rows = fetch_weather_current()
    except NoTargetGridError as exc:
        logging.warning("날씨 실황 수집 건너뜀: %s", exc)
        return

    saved = save_weather_current(rows)
    logging.info("날씨 실황 수집=%d, DB 적재=%d", len(rows), saved)


@app.timer_trigger(
    schedule=WEATHER_FORECAST_SCHEDULE,
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def collect_weather_forecast_timer(timer: func.TimerRequest) -> None:
    if timer.past_due:
        logging.warning("collect_weather_forecast_timer 실행이 지연되었습니다.")

    try:
        rows = fetch_weather_forecast()
    except NoTargetGridError as exc:
        logging.warning("날씨 예보 수집 건너뜀: %s", exc)
        return
    
    saved = save_weather_forecast(rows)
    logging.info("날씨 예보 수집=%d, DB 적재=%d", len(rows), saved)
