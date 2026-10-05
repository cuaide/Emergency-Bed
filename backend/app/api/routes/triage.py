"""증상 분류 API. 텍스트/음성 입력을 에이전트에 넘기고 안내 음성을 돌려준다.

라우트를 일부러 동기(def)로 둔다. Speech SDK와 에이전트 호출은 수 초가 걸리는
블로킹 호출이라 async 안에서 그대로 부르면 이벤트 루프가 멈춰 다른 요청까지 막힌다.
동기 def로 두면 FastAPI가 스레드풀에서 실행한다.

서비스 계층의 도메인 예외(SpeechError / TriageAgentError)를 HTTP 상태로 바꾸는 일은
이 파일에서만 한다.
"""

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

# 에이전트 결과에 안내 문구가 없을 때 읽어 줄 기본 문장
DEFAULT_GUIDE_MESSAGE = "응급 안내를 시작합니다."

# 몇 초짜리 발화면 충분하다. 이보다 큰 업로드는 받지 않는다.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _run_agent(message: str) -> dict:
    try:
        return triage_service.call_triage_agent(message)
    except triage_service.TriageAgentNotConfigured as exc:
        # 설정 누락은 서버 문제다.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc
    except triage_service.TriageAgentError as exc:
        # 호출 실패나 형식 위반은 상류(에이전트) 문제다.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


def _audio_url(request: Request, triage_result: dict) -> str | None:
    """안내 음성을 만들고 다운로드 경로를 돌려준다.

    합성 실패는 로그만 남기고 None을 반환한다. 음성은 부가 기능이라
    분류 결과까지 같이 버릴 이유가 없다.
    """
    message = triage_result.get("user_message") or DEFAULT_GUIDE_MESSAGE
    try:
        path = speech_service.text_to_speech(str(message))
    except speech_service.SpeechError as exc:
        logger.warning("안내 음성 합성 실패: %s", exc)
        return None
    # 라우터 prefix가 바뀌어도 따라가도록 경로를 역참조한다.
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

    # async 라우트가 아니므로 await 없이 동기로 읽는다.
    # 상한보다 1바이트 더 읽어 초과 여부를 판별한다.
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
    """합성된 안내 음성을 돌려준다.

    파일명 검증은 speech_service.resolve_audio_path 가 한다. 검증 없이 경로를 쓰면
    작업 디렉터리의 local.settings.json / .env 가 그대로 나간다.
    """
    path = speech_service.resolve_audio_path(filename)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="음성 파일을 찾을 수 없습니다.",
        )
    return FileResponse(path, media_type="audio/wav", filename=filename)
