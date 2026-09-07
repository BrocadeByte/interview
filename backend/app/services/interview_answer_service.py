import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.interview import InterviewMessage, InterviewSession


ANSWER_LEASE_SECONDS = 180
LEASE_RENEW_INTERVAL_SECONDS = 30


class SessionLeaseLostError(RuntimeError):
    pass


class SessionLeaseHeartbeat:
    """通过独立数据库会话定期续约的面试处理心跳。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        session_id: int,
        request_id: str,
        interval_seconds: float = LEASE_RENEW_INTERVAL_SECONDS,
    ) -> None:
        """保存会话工厂、租约归属和续期间隔，初始化后台任务及租约丢失标记。"""
        self._session_factory = session_factory
        self._session_id = session_id
        self._request_id = request_id
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._lost = asyncio.Event()

    @property
    def lost(self) -> bool:
        """返回后台心跳是否已确认当前处理者失去租约。"""
        return self._lost.is_set()

    def start(self) -> None:
        """尚未启动时创建后台续约任务，避免重复启动心跳。"""
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """取消并等待后台续约任务结束，再清除任务引用。"""
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        """按间隔使用独立会话续约，暂时异常时继续尝试，确认失去租约时记录并停止。"""
        while True:
            await asyncio.sleep(self._interval_seconds)
            try:
                async with self._session_factory() as db:
                    renewed = await renew_answer_lease(
                        db,
                        session_id=self._session_id,
                        request_id=self._request_id,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                # 最终写入仍以租约归属检查为准，确保失去租约的处理者无法提交。
                continue
            if not renewed:
                self._lost.set()
                return



async def find_completed_answer_request(
    db: AsyncSession,
    *,
    session_id: int,
    request_id: str,
) -> InterviewMessage | None:
    """通过已落库的用户消息判断 request_id 是否处理完成，作为幂等重试凭据。"""
    return await db.scalar(
        select(InterviewMessage).where(
            InterviewMessage.session_id == session_id,
            InterviewMessage.role == "user",
            InterviewMessage.request_id == request_id,
        )
    )


async def acquire_answer_lease(
    db: AsyncSession,
    *,
    session_id: int,
    user_id: int,
    request_id: str,
    now: datetime | None = None,
    lease_seconds: int = ANSWER_LEASE_SECONDS,
    allowed_statuses: tuple[str, ...] = ("active",),
) -> bool:
    """原子占用会话处理租约，确保同一面试同时只有一个回答或结束请求在运行。

    租约超时后允许新请求接管，避免进程崩溃永久锁住会话。条件更新的 rowcount
    是唯一成功依据，提交后其他数据库会话才能观察到占用状态。
    """
    acquired_at = now or datetime.now(timezone.utc).replace(tzinfo=None)
    expired_before = acquired_at - timedelta(seconds=lease_seconds)
    result = await db.execute(
        update(InterviewSession)
        .where(
            InterviewSession.id == session_id,
            InterviewSession.user_id == user_id,
            InterviewSession.status.in_(allowed_statuses),
            or_(
                InterviewSession.processing_request_id.is_(None),
                InterviewSession.processing_started_at.is_(None),
                InterviewSession.processing_started_at < expired_before,
            ),
        )
        .values(processing_request_id=request_id, processing_started_at=acquired_at)
    )
    await db.commit()
    return result.rowcount == 1


async def release_answer_lease(
    db: AsyncSession,
    *,
    session_id: int,
    request_id: str,
    commit: bool = True,
    require_held: bool = False,
) -> bool:
    """仅由持有相同 request_id 的请求释放租约，防止旧请求清除新请求的锁。"""
    result = await db.execute(
        update(InterviewSession)
        .where(
            and_(
                InterviewSession.id == session_id,
                InterviewSession.processing_request_id == request_id,
            )
        )
        .values(processing_request_id=None, processing_started_at=None)
    )
    released = result.rowcount == 1
    if require_held and not released:
        raise SessionLeaseLostError("Interview session lease ownership was lost")
    if commit:
        await db.commit()
    return released


async def renew_answer_lease(
    db: AsyncSession,
    *,
    session_id: int,
    request_id: str,
    now: datetime | None = None,
) -> bool:
    """仅更新当前租约持有者的时间戳，防止旧处理者续约已被接管的任务。"""
    renewed_at = now or datetime.now(timezone.utc).replace(tzinfo=None)
    result = await db.execute(
        update(InterviewSession)
        .where(
            InterviewSession.id == session_id,
            InterviewSession.processing_request_id == request_id,
        )
        .values(processing_started_at=renewed_at)
    )
    await db.commit()
    return result.rowcount == 1
