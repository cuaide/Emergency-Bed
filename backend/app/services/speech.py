"""Azure AI Speech 음성 인식(STT) / 음성 합성(TTS).

Tmap과 같은 성격이라 수집 파이프라인에 넣지 않는다. 사용자의 음성은 요청 시점에만
존재해 미리 적재할 수 없기 때문이다 (services/tmap.py 참고).

SDK는 함수 안에서 import 한다. Function App 배포본에는 이 패키지가 없어도 되고
테스트도 설치 없이 돌아간다 (eventhub.send_to_event_hub 와 같은 방식).

이 모듈은 HTTPException을 던지지 않는다. FastAPI에 묶이면 Function이나 CLI에서
재사용할 수 없기 때문이며, HTTP 변환은 api/routes/triage.py 가 담당한다.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

# 합성 결과 파일명 규칙. 다운로드 엔드포인트가 임의 경로를 읽지 못하게 막는 1차 방어선이다.
AUDIO_FILENAME = re.compile(r"^triage_voice_[0-9a-f]{8}\.wav$")

# 업로드본은 이 접두사를 쓴다. AUDIO_FILENAME에 걸리지 않으므로 다운로드가 불가능하다.
UPLOAD_PREFIX = "upload_"


class SpeechError(RuntimeError):
    """Speech SDK 호출이 실패했거나 SPEECH_KEY가 없는 경우."""


def is_configured() -> bool:
    return bool(get_settings().speech_key)


def audio_dir() -> Path:
    """음성 파일이 머무는 전용 디렉터리. 없으면 만든다.

    작업 디렉터리(backend/)에는 local.settings.json / .env 처럼 절대 내보내면 안 되는
    파일이 있다. 음성만 따로 담아 두어야 다운로드 경로가 그쪽을 넘볼 수 없다.
    """
    path = Path(get_settings().audio_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_audio_path() -> Path:
    """합성 결과를 저장할 새 경로 (파일명 충돌 없음)."""
    return audio_dir() / f"triage_voice_{uuid.uuid4().hex[:8]}.wav"


def new_upload_path() -> Path:
    """업로드된 음성을 잠시 둘 경로."""
    return audio_dir() / f"{UPLOAD_PREFIX}{uuid.uuid4().hex[:8]}.wav"


def resolve_audio_path(filename: str) -> Path | None:
    """다운로드 요청의 파일명을 검증해 실제 경로로 바꾼다. 부적합하면 None.

    이름 패턴과 실제 위치를 모두 확인한다. 패턴만 보면 심볼릭 링크로,
    위치만 보면 업로드 임시본으로 빠져나갈 수 있다.
    """
    if not AUDIO_FILENAME.match(filename):
        return None
    base = audio_dir().resolve()
    path = (base / filename).resolve()
    if path.parent != base or not path.is_file():
        return None
    return path


def purge_old_audio(max_age_minutes: int | None = None) -> int:
    """오래된 음성 파일을 지우고 삭제 건수를 반환한다.

    합성 결과를 디스크에 남기는 구조라 그냥 두면 계속 쌓인다. 요청마다 한 번씩
    호출해 별도 정리 작업 없이 유지한다. 실패는 조용히 넘긴다 — 정리는 부가 작업이라
    본 요청을 깨서는 안 된다.
    """
    settings = get_settings()
    ttl = settings.audio_ttl_minutes if max_age_minutes is None else max_age_minutes
    if ttl <= 0:
        return 0

    cutoff = time.time() - ttl * 60
    removed = 0
    try:
        entries = list(audio_dir().iterdir())
    except OSError as exc:
        logger.debug("음성 디렉터리 조회 실패: %s", exc)
        return 0

    for entry in entries:
        if entry.suffix != ".wav":
            continue
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError:  # 다른 요청이 먼저 지웠거나 사용 중
            continue
    if removed:
        logger.info("오래된 음성 파일 %d건 삭제", removed)
    return removed


def _speech_sdk():
    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError as exc:  # pragma: no cover - 설치 여부에 따라 갈림
        raise SpeechError(
            "azure-cognitiveservices-speech 패키지가 설치돼 있지 않습니다. "
            "pip install -r requirements.txt 를 실행하세요."
        ) from exc
    return speechsdk


def _speech_config(*, for_synthesis: bool):
    """SDK 모듈과 SpeechConfig를 함께 돌려준다 (import를 두 번 하지 않기 위해)."""
    settings = get_settings()
    if not settings.speech_key:
        raise SpeechError("SPEECH_KEY 환경 변수가 비어 있습니다.")

    speechsdk = _speech_sdk()
    config = speechsdk.SpeechConfig(
        subscription=settings.speech_key,
        region=settings.speech_region,
    )
    if for_synthesis:
        config.speech_synthesis_voice_name = settings.speech_voice
    else:
        config.speech_recognition_language = settings.speech_language
    return speechsdk, config


def _cancellation_detail(result) -> str:
    details = getattr(result, "cancellation_details", None)
    return (getattr(details, "error_details", "") or "").strip()


def speech_to_text(audio_path: str | Path) -> str:
    """음성 파일 1건을 한국어 텍스트로 변환한다.

    수 초가 걸리는 블로킹 호출이다. 호출하는 라우트는 반드시 동기(def)여야 한다.
    """
    speechsdk, config = _speech_config(for_synthesis=False)

    audio_config = speechsdk.audio.AudioConfig(filename=str(audio_path))
    recognizer = speechsdk.SpeechRecognizer(speech_config=config, audio_config=audio_config)
    result = recognizer.recognize_once_async().get()

    if result.reason == speechsdk.ResultReason.RecognizedSpeech:
        return result.text
    if result.reason == speechsdk.ResultReason.NoMatch:
        raise SpeechError("음성에서 텍스트를 인식하지 못했습니다.")

    detail = _cancellation_detail(result)
    raise SpeechError(f"음성 인식에 실패했습니다. {detail}".strip())


def text_to_speech(text: str, output_path: str | Path | None = None) -> Path:
    """텍스트를 WAV로 합성하고 저장된 경로를 반환한다.

    output_path를 주지 않으면 audio_dir() 아래 고유 이름으로 만든다.
    """
    message = (text or "").strip()
    if not message:
        raise SpeechError("합성할 텍스트가 비어 있습니다.")

    speechsdk, config = _speech_config(for_synthesis=True)

    path = Path(output_path) if output_path else new_audio_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    audio_config = speechsdk.audio.AudioOutputConfig(filename=str(path))
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=audio_config)
    result = synthesizer.speak_text_async(message).get()

    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        detail = _cancellation_detail(result)
        raise SpeechError(f"음성 합성에 실패했습니다. {detail}".strip())
    return path
