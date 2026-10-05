from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from app.schemas import TriageRequest, TriageResponse
from app.services import speech as speech_service
from app.services import triage as triage_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/triage", tags=["triage"])
DEFAULT_GUIDE_MESSAGE = "응급 안내를 시작합니다."
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _run_agent(message: str) -> dict:
    try:
        return triage_service.call_triage_agent(message)
    except triage_service.TriageAgentNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc
    except triage_service.TriageAgentError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


def _audio_url(request: Request, triage_result: dict) -> str | None:
    message = triage_result.get("user_message") or DEFAULT_GUIDE_MESSAGE
    try:
        path = speech_service.text_to_speech(str(message))
    except speech_service.SpeechError as exc:
        logger.warning("안내 음성 합성 실패: %s", exc)
        return None
    return request.url_for("get_audio_file", filename=path.name).path


@router.post("/text", response_model=TriageResponse, summary="텍스트 증상 분류")
def triage_by_text(req: TriageRequest, request: Request) -> TriageResponse:
    speech_service.purge_old_audio()
    result = _run_agent(req.message)
    return TriageResponse(
        triage=result,
        audio_url=_audio_url(request, result) if req.generate_audio else None,
    )


@router.post("/voice", response_model=TriageResponse, summary="음성 증상 분류 (STT → 에이전트 → TTS)")
def triage_by_voice(
    request: Request,
    file: Annotated[UploadFile, File(description="증상을 말한 WAV 파일")],
    generate_audio: Annotated[bool, Form()] = True,
) -> TriageResponse:
    speech_service.purge_old_audio()
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="업로드된 음성 파일이 비어 있습니다.",
        )
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"음성 파일은 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 이하만 처리합니다.",
        )

    upload_path = speech_service.new_upload_path()
    upload_path.write_bytes(data)
    try:
        recognized = speech_service.speech_to_text(upload_path)
    except speech_service.SpeechError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    finally:
        upload_path.unlink(missing_ok=True)

    result = _run_agent(recognized)
    return TriageResponse(
        recognized_text=recognized,
        triage=result,
        audio_url=_audio_url(request, result) if generate_audio else None,
    )

@router.get("/audio/{filename}", summary="안내 음성 다운로드")
def get_audio_file(filename: str) -> FileResponse:
    path = speech_service.resolve_audio_path(filename)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="음성 파일을 찾을 수 없습니다.",
        )
    return FileResponse(path, media_type="audio/wav", filename=filename)
