from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import AppContainer, get_container
from api.handlers import (
    analyze_only,
    generate_content_only,
    generate_topics_only,
    run_agent_pipeline,
)
from core.config import settings
from services.trace_service import load_spans
from models.schemas import (
    AgentRunRequest,
    AgentRunResponse,
    AnalyzeRequest,
    AnalyzeResponse,
    ContentGenerateRequest,
    ContentGenerateResponse,
    TopicGenerateRequest,
    TopicGenerateResponse,
    TraceResponse,
)


router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "app_name": settings.app_name, "version": settings.app_version}


@router.post("/analysis/analyze", response_model=AnalyzeResponse)
async def analyze_endpoint(request: AnalyzeRequest, c: AppContainer = Depends(get_container)):
    try:
        return await analyze_only(c, request)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/topics/generate", response_model=TopicGenerateResponse)
async def topic_endpoint(request: TopicGenerateRequest, c: AppContainer = Depends(get_container)):
    try:
        return await generate_topics_only(c, request)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/content/generate", response_model=ContentGenerateResponse)
async def content_endpoint(request: ContentGenerateRequest, c: AppContainer = Depends(get_container)):
    try:
        return await generate_content_only(c, request)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/agent/run", response_model=AgentRunResponse)
async def run_agent_endpoint(request: AgentRunRequest, c: AppContainer = Depends(get_container)):
    try:
        return await run_agent_pipeline(c, request)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/trace/{run_id}", response_model=TraceResponse)
async def trace_endpoint(run_id: str, c: AppContainer = Depends(get_container)):
    state = c.session_service.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="run_id not found")

    total_tokens = sum(t.input_tokens + t.output_tokens for t in state.traces)
    total_latency = round(sum(t.latency_ms or 0 for t in state.traces), 2)
    return TraceResponse(
        run_id=state.run_id,
        stage=state.stage,
        success=not state.failed,
        total_tokens=total_tokens,
        total_latency_ms=total_latency,
        nodes=state.traces,
        spans=load_spans(state.run_id),
    )


@router.get("/memory/{user_id}/sessions")
async def memory_sessions_endpoint(user_id: str, c: AppContainer = Depends(get_container)):
    """查询某用户的所有历史 session 列表（含关键词索引）。"""
    try:
        sessions = c.memory_manager.storage.load_user_sessions(user_id)
        return {"user_id": user_id, "sessions": sessions}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/memory/{user_id}/recent")
async def memory_recent_endpoint(
    user_id: str,
    session_id: str,
    c: AppContainer = Depends(get_container),
):
    """查询某 session 的最近原始对话（前端展示用）。"""
    try:
        turns = c.memory_manager.get_frontend_recent_turns(user_id, session_id)
        return {"user_id": user_id, "session_id": session_id, "messages": turns}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
