from __future__ import annotations

import logging
import re
import time
import uuid
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)
AUDIO_FILENAME = re.compile(r"^triage_voice_[0-9a-f]{8}\.wav$")
UPLOAD_PREFIX = "upload_"


class SpeechError(RuntimeError):
    """Speech SDK 호출이 실패했거나 SPEECH_KEY가 없는 경우."""


def is_configured() -> bool:
    return bool(get_settings().speech_key)


def audio_dir() -> Path:
    path = Path(get_settings().audio_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_audio_path() -> Path:
    return audio_dir() / f"triage_voice_{uuid.uuid4().hex[:8]}.wav"


def new_upload_path() -> Path:
    return audio_dir() / f"{UPLOAD_PREFIX}{uuid.uuid4().hex[:8]}.wav"


def resolve_audio_path(filename: str) -> Path | None:
    if not AUDIO_FILENAME.match(filename):
        return None
    base = audio_dir().resolve()
    path = (base / filename).resolve()
    if path.parent != base or not path.is_file():
        return None
    return path


def purge_old_audio(max_age_minutes: int | None = None) -> int:
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
        except OSError:  
            continue
    if removed:
        logger.info("오래된 음성 파일 %d건 삭제", removed)
    return removed


def _speech_sdk():
    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError as exc: 
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
