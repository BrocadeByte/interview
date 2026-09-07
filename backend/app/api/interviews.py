"""面试 HTTP 接口。

路由层只负责请求参数、鉴权依赖、状态码和 SSE 传输；会话查询、事务和 Agent
状态推进统一委托给 ``interview_service``。
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.background import BackgroundTask

from app.agents.graph import interview_graph  # 保留导出，兼容现有测试与调试脚本。
from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.interview import InterviewMessage, InterviewSession
from app.models.profile import UserProfile
from app.models.user import User
from app.schemas.interview import (
    InterviewAnswer,
    InterviewCreate,
    InterviewListItem,
    InterviewSessionRead,
    InterviewWarmup,
)
from app.schemas.question_review import QuestionReviewRead
from app.schemas.report import InterviewReportRead
from app.schemas.score import InterviewScoreRead
from app.services import interview_service
from app.services.interview_answer_service import (
    SessionLeaseHeartbeat,
    SessionLeaseLostError,
    acquire_answer_lease,
    find_completed_answer_request,
    release_answer_lease,
)
from app.services.interview_memory_service import maybe_compact_medium_term_memory
from app.services.llm_stream import (
    publish_committed_text,
    reset_stream_delta_callback,
    reset_stream_text_done_callback,
    set_stream_delta_callback,
    set_stream_text_done_callback,
)


router = APIRouter(prefix="/interviews", tags=["interviews"])
logger = logging.getLogger(__name__)


def _planner_knowledge_query(target_position: str) -> str:
    """构造岗位相关的预热查询；保留此入口便于独立调试。"""
    return interview_service.planner_knowledge_query(target_position)


async def _warmup_interview_dependencies(target_position: str) -> None:
    """后台预热面试依赖，预热失败不会阻断用户创建面试。"""
    await interview_service.warmup_interview_dependencies(target_position)


@router.post("/warmup", status_code=status.HTTP_204_NO_CONTENT)
async def warmup_interview(
    payload: InterviewWarmup,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
) -> None:
    """用户填写表单时异步预热目标岗位所需的检索资源。"""
    del current_user
    background_tasks.add_task(_warmup_interview_dependencies, payload.target_position)


@router.post("", response_model=InterviewSessionRead, status_code=status.HTTP_201_CREATED)
async def create_interview(
    payload: InterviewCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> InterviewSession:
    """接收创建请求，业务校验和持久化由 Service 完成。"""
    return await interview_service.create_interview_session(
        db,
        user_id=current_user.id,
        payload=payload,
    )


@router.post("/{session_id}/start", response_model=InterviewSessionRead)
async def start_interview(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> InterviewSession:
    """非流式启动面试；重复请求直接返回已经生成的首题。"""
    session = await _load_session(db, current_user.id, session_id)
    if session.status == "finished":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Interview is already finished",
        )
    if session.messages:
        return await interview_service.resume_started_session(db, session)

    start_request_id = str(uuid4())
    acquired = await acquire_answer_lease(
        db,
        session_id=session_id,
        user_id=current_user.id,
        request_id=start_request_id,
        allowed_statuses=("preparing",),
    )
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Interview start is already being processed",
        )

    session_factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)
    heartbeat = SessionLeaseHeartbeat(
        session_factory,
        session_id=session_id,
        request_id=start_request_id,
    )
    try:
        return await _process_start(
            db,
            current_user,
            session_id,
            start_request_id,
            heartbeat,
        )
    except SessionLeaseLostError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/{session_id}/start/stream")
async def start_interview_stream(
    session_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """以 SSE 启动面试，业务提交后流式推送权威首题文本。"""
    session = await _load_session(db, current_user.id, session_id)
    if session.status == "finished":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Interview is already finished",
        )
    if session.messages:
        loaded = await interview_service.resume_started_session(db, session)
        return _sse_response(_completed_session_stream(loaded))

    start_request_id = str(uuid4())
    acquired = await acquire_answer_lease(
        db,
        session_id=session_id,
        user_id=current_user.id,
        request_id=start_request_id,
        allowed_statuses=("preparing",),
    )
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Interview start is already being processed",
        )

    # 流式响应可能晚于请求依赖释放，因此使用独立数据库会话处理整段生成过程。
    stream_session_factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)
    await db.close()

    async def event_stream() -> AsyncIterator[str]:
        """在独立数据库会话中启动面试并转发流式事件，处理取消和异常后的回滚。"""
        queue: asyncio.Queue[str] = asyncio.Queue()
        async with stream_session_factory() as stream_db:

            async def emit_delta(delta: str) -> None:
                """将新增的问题文本片段封装为增量事件并放入发送队列。"""
                await queue.put(_sse_event("delta", {"content": delta}))

            async def emit_text_done(content: str) -> None:
                """将已完成的问题文本封装为文本完成事件并放入发送队列。"""
                await queue.put(_sse_event("text_done", {"content": content}))

            async def process() -> InterviewSession:
                """注册流式回调并执行带租约心跳的面试启动，发布已提交问题后恢复回调上下文。"""
                delta_token = set_stream_delta_callback(emit_delta)
                done_token = set_stream_text_done_callback(emit_text_done)
                heartbeat = SessionLeaseHeartbeat(
                    stream_session_factory,
                    session_id=session_id,
                    request_id=start_request_id,
                )
                try:
                    committed = await _process_start(
                        stream_db,
                        current_user,
                        session_id,
                        start_request_id,
                        heartbeat,
                    )
                    await publish_committed_text(committed.messages[-1].content)
                    return committed
                finally:
                    reset_stream_text_done_callback(done_token)
                    reset_stream_delta_callback(delta_token)

            task = asyncio.create_task(process())
            yield _sse_event("status", {"phase": "retrieving"})
            try:
                async for event in _forward_stream_events(request, queue, task):
                    yield event
                result = await task
                yield _sse_event("complete", _serialize_session(result))
            except asyncio.CancelledError:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                await stream_db.rollback()
                raise
            except Exception as exc:
                await stream_db.rollback()
                logger.exception("interview.start.stream.failed session_id=%s", session_id)
                yield _sse_event("error", {"message": str(exc) or "Streaming start failed"})

    return _sse_response(
        event_stream(),
        background=BackgroundTask(
            _release_stream_lease,
            stream_session_factory,
            session_id,
            start_request_id,
        ),
    )


@router.get("", response_model=list[InterviewListItem])
async def list_interviews(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[InterviewSession]:
    """返回当前用户的面试列表。"""
    return await interview_service.list_interview_sessions(db, current_user.id)


@router.get("/{session_id}", response_model=InterviewSessionRead)
async def get_interview(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> InterviewSession:
    """返回指定面试的完整状态和消息。"""
    return await _load_session(db, current_user.id, session_id)


@router.get("/{session_id}/scores", response_model=list[InterviewScoreRead])
async def get_interview_scores(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[InterviewScoreRead]:
    """返回指定面试的逐题评分。"""
    return await interview_service.get_interview_scores(
        db,
        user_id=current_user.id,
        session_id=session_id,
    )


@router.get("/{session_id}/question-reviews", response_model=list[QuestionReviewRead])
async def get_interview_question_reviews(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[QuestionReviewRead]:
    """返回正式面试报告对应的逐题复盘快照。"""
    return await interview_service.get_interview_question_reviews(
        db,
        user_id=current_user.id,
        session_id=session_id,
    )


@router.get("/{session_id}/report", response_model=InterviewReportRead)
async def get_interview_report(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> InterviewReportRead:
    """获取或生成指定面试的报告。"""
    return await interview_service.get_interview_report(
        db,
        user_id=current_user.id,
        session_id=session_id,
    )


@router.post("/{session_id}/answer", response_model=InterviewSessionRead)
async def answer_interview(
    session_id: int,
    payload: InterviewAnswer,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> InterviewSession:
    """处理非流式回答；request_id 保证重试幂等，租约阻止并发串线。"""
    request_id = str(payload.request_id)
    session = await _load_session(db, current_user.id, session_id)
    completed_request = await find_completed_answer_request(
        db,
        session_id=session_id,
        request_id=request_id,
    )
    if completed_request:
        return session
    _ensure_answerable(session)

    acquired = await acquire_answer_lease(
        db,
        session_id=session_id,
        user_id=current_user.id,
        request_id=request_id,
    )
    if not acquired:
        # 获取租约失败后再查一次幂等结果，覆盖并发请求刚好完成的竞态窗口。
        completed_request = await find_completed_answer_request(
            db,
            session_id=session_id,
            request_id=request_id,
        )
        if completed_request:
            return await _load_session(db, current_user.id, session_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another answer is being processed for this interview",
        )

    try:
        session = await _load_session(db, current_user.id, session_id)
        return await _process_answer(
            db,
            session,
            current_user,
            payload.answer,
            request_id,
        )
    except Exception:
        await db.rollback()
        await release_answer_lease(db, session_id=session_id, request_id=request_id)
        raise


@router.post("/{session_id}/answer/stream")
async def answer_interview_stream(
    session_id: int,
    payload: InterviewAnswer,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """以 SSE 处理回答，业务提交后推送权威文本和完整会话状态。"""
    request_id = str(payload.request_id)
    session = await _load_session(db, current_user.id, session_id)
    completed_request = await find_completed_answer_request(
        db,
        session_id=session_id,
        request_id=request_id,
    )
    if completed_request:
        return _sse_response(_completed_session_stream(session))
    _ensure_answerable(session)

    acquired = await acquire_answer_lease(
        db,
        session_id=session_id,
        user_id=current_user.id,
        request_id=request_id,
    )
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another answer is being processed for this interview",
        )

    stream_session_factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)
    await db.close()

    async def event_stream() -> AsyncIterator[str]:
        """在独立数据库会话中处理回答并转发流式事件，取消或失败时回滚并释放回答租约。"""
        queue: asyncio.Queue[str] = asyncio.Queue()
        async with stream_session_factory() as stream_db:

            async def emit_delta(delta: str) -> None:
                """将新增的问题文本片段封装为增量事件并放入发送队列。"""
                await queue.put(_sse_event("delta", {"content": delta}))

            async def emit_text_done(content: str) -> None:
                """将已完成的问题文本封装为文本完成事件并放入发送队列。"""
                await queue.put(_sse_event("text_done", {"content": content}))

            async def process() -> InterviewSession:
                """加载面试会话并注册流式回调，处理回答、发布已提交问题后恢复回调上下文。"""
                active_session = await _load_session(stream_db, current_user.id, session_id)
                delta_token = set_stream_delta_callback(emit_delta)
                done_token = set_stream_text_done_callback(emit_text_done)
                try:
                    committed = await _process_answer(
                        stream_db,
                        active_session,
                        current_user,
                        payload.answer,
                        request_id,
                    )
                    await publish_committed_text(committed.messages[-1].content)
                    return committed
                finally:
                    reset_stream_text_done_callback(done_token)
                    reset_stream_delta_callback(delta_token)

            task = asyncio.create_task(process())
            yield _sse_event("status", {"phase": "evaluating"})
            try:
                async for event in _forward_stream_events(request, queue, task):
                    yield event
                result = await task
                yield _sse_event("complete", _serialize_session(result))
            except asyncio.CancelledError:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                await stream_db.rollback()
                await release_answer_lease(
                    stream_db,
                    session_id=session_id,
                    request_id=request_id,
                )
                raise
            except Exception as exc:
                await stream_db.rollback()
                await release_answer_lease(
                    stream_db,
                    session_id=session_id,
                    request_id=request_id,
                )
                logger.exception("interview.answer.stream.failed session_id=%s", session_id)
                yield _sse_event("error", {"message": str(exc) or "Streaming answer failed"})

    return _sse_response(event_stream())


@router.post("/{session_id}/finish", response_model=InterviewSessionRead)
async def finish_interview(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> InterviewSession:
    """主动结束面试；Service 使用回答租约保证结束操作与评分互斥。"""
    return await interview_service.finish_interview_session(
        db,
        user_id=current_user.id,
        session_id=session_id,
        request_id=str(uuid4()),
    )


async def _process_answer(
    db: AsyncSession,
    session: InterviewSession,
    current_user: User,
    answer: str,
    request_id: str,
) -> InterviewSession:
    """兼容入口：把 API 当前注入的依赖传给面试业务服务。"""
    return await interview_service.process_answer(
        db,
        session,
        user_id=current_user.id,
        answer=answer,
        request_id=request_id,
        compact_memory=maybe_compact_medium_term_memory,
    )


async def _process_start(
    db: AsyncSession,
    current_user: User,
    session_id: int,
    request_id: str,
    heartbeat: SessionLeaseHeartbeat,
) -> InterviewSession:
    """兼容入口：在 Service 中完成首题生成事务。"""
    return await interview_service.process_start(
        db,
        user_id=current_user.id,
        session_id=session_id,
        request_id=request_id,
        heartbeat=heartbeat,
    )


async def _load_session(
    db: AsyncSession,
    user_id: int,
    session_id: int,
) -> InterviewSession:
    """兼容入口：加载当前用户拥有的面试会话。"""
    return await interview_service.load_interview_session(db, user_id, session_id)


async def _record_interview_started(db: AsyncSession, session: InterviewSession) -> None:
    """兼容入口：记录面试开始事件。"""
    await interview_service.record_interview_started(db, session)


async def _record_interview_finished(db: AsyncSession, session: InterviewSession) -> None:
    """兼容入口：记录面试结束事件。"""
    await interview_service.record_interview_finished(db, session)


async def _get_profile(db: AsyncSession, user_id: int) -> UserProfile | None:
    """兼容入口：读取用户画像。"""
    return await interview_service.get_user_profile(db, user_id)


def parse_interview_plan(plan_json: str | None) -> list[dict]:
    """兼容入口：安全解析面试计划 JSON。"""
    return interview_service.parse_interview_plan(plan_json)


def attach_current_plan_fields(session: InterviewSession) -> None:
    """兼容入口：补充会话响应所需的当前计划字段。"""
    interview_service.attach_current_plan_fields(session)


def _validated_visible_question(value: object) -> str:
    """委托面试服务校验并返回可展示给用户的问题文本。"""
    return interview_service.validated_visible_question(value)


def _is_duplicate_question(question: str, messages: list[InterviewMessage]) -> bool:
    """委托面试服务检查候选问题是否与历史面试问题重复。"""
    return interview_service.is_duplicate_question(question, messages)


def _ensure_answerable(session: InterviewSession) -> None:
    """把会话状态转换为稳定的 HTTP 错误，避免两种回答接口重复判断。"""
    if session.status == "preparing":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Interview is still preparing",
        )
    if session.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Interview is not active",
        )


async def _forward_stream_events(
    request: Request,
    queue: asyncio.Queue[str],
    task: asyncio.Task[InterviewSession],
) -> AsyncIterator[str]:
    """转发模型增量；空闲时发送心跳，客户端断开时取消后台任务。"""
    while not task.done():
        if await request.is_disconnected():
            task.cancel()
            break
        try:
            yield await asyncio.wait_for(queue.get(), timeout=15)
        except asyncio.TimeoutError:
            yield ": keep-alive\n\n"

    # 任务结束与队列写入可能几乎同时发生，完成前必须排空剩余增量。
    while not queue.empty():
        yield queue.get_nowait()


def _sse_response(
    events: AsyncIterator[str],
    *,
    background: BackgroundTask | None = None,
) -> StreamingResponse:
    """创建禁用代理缓冲的 SSE 响应，确保增量及时到达浏览器。"""
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        background=background,
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _release_stream_lease(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: int,
    request_id: str,
) -> None:
    """流式响应结束后使用独立会话兜底释放租约。"""
    async with session_factory() as db:
        await release_answer_lease(db, session_id=session_id, request_id=request_id)


def _sse_event(event: str, data: object) -> str:
    """按 SSE 帧格式序列化事件，JSON 保留中文并使用紧凑编码。"""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def _serialize_session(session: InterviewSession) -> dict:
    """把 ORM 会话转换为可安全 JSON 序列化的响应数据。"""
    return InterviewSessionRead.model_validate(session).model_dump(mode="json")


async def _completed_session_stream(session: InterviewSession) -> AsyncIterator[str]:
    """为幂等命中的流式请求只发送最终完成事件。"""
    yield _sse_event("complete", _serialize_session(session))
