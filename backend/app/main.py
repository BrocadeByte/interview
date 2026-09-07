import asyncio
from contextlib import asynccontextmanager, suppress
from collections.abc import AsyncGenerator
import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

import app.models  # noqa: F401  导入模型以注册表元数据，保留未使用导入的检查豁免。
from app.api import analytics, auth, interviews, job_descriptions, knowledge, knowledge_async, practice, profiles, reports, resumes
from app.core.config import settings
from app.core.database import Base, engine
from app.rag.embeddings import close_embedding_session
from app.rag.retriever import close_retriever_client, warm_retriever
from app.services.health_service import readiness_status


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """启动时建表、补齐旧表字段并异步预热检索器，退出时取消未完成预热并关闭外部连接。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await ensure_user_columns(conn)
        await ensure_knowledge_document_columns(conn)
        await ensure_interview_message_columns(conn)
        await ensure_interview_session_columns(conn)
        await ensure_interview_report_columns(conn)
        await ensure_interview_score_columns(conn)
    qdrant_warmup_task = asyncio.create_task(warm_retriever())
    try:
        yield
    finally:
        if not qdrant_warmup_task.done():
            qdrant_warmup_task.cancel()
            with suppress(asyncio.CancelledError):
                await qdrant_warmup_task
        await close_retriever_client()
        await close_embedding_session()


async def ensure_user_columns(conn) -> None:
    """检查用户表并为旧数据库补充管理员标记字段。"""
    result = await conn.execute(text("SHOW COLUMNS FROM users"))
    existing_columns = {row[0] for row in result.fetchall()}
    migrations = {"is_admin": "ALTER TABLE users ADD COLUMN is_admin BOOL NOT NULL DEFAULT FALSE"}
    for column, statement in migrations.items():
        if column not in existing_columns:
            await conn.execute(text(statement))


async def ensure_knowledge_document_columns(conn) -> None:
    """扩展知识正文的存储类型，并补齐文件来源、解析状态和索引版本等字段。"""
    column_type = await conn.execute(
        text(
            "SELECT DATA_TYPE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'knowledge_documents' AND COLUMN_NAME = 'content'"
        )
    )
    current_type = column_type.scalar()
    if current_type and current_type.lower() != "longtext":
        await conn.execute(text("ALTER TABLE knowledge_documents MODIFY COLUMN content LONGTEXT NOT NULL"))
    result = await conn.execute(text("SHOW COLUMNS FROM knowledge_documents"))
    existing_columns = {row[0] for row in result.fetchall()}
    migrations = {
        "original_oss_key": "ALTER TABLE knowledge_documents ADD COLUMN original_oss_key VARCHAR(512) NULL",
        "parsed_text_oss_key": "ALTER TABLE knowledge_documents ADD COLUMN parsed_text_oss_key VARCHAR(512) NULL",
        "file_name": "ALTER TABLE knowledge_documents ADD COLUMN file_name VARCHAR(255) NULL",
        "file_type": "ALTER TABLE knowledge_documents ADD COLUMN file_type VARCHAR(32) NULL",
        "file_size": "ALTER TABLE knowledge_documents ADD COLUMN file_size INT NULL",
        "chunk_count": "ALTER TABLE knowledge_documents ADD COLUMN chunk_count INT NOT NULL DEFAULT 0",
        "index_version": "ALTER TABLE knowledge_documents ADD COLUMN index_version INT NOT NULL DEFAULT 1",
        "parse_status": "ALTER TABLE knowledge_documents ADD COLUMN parse_status VARCHAR(32) NOT NULL DEFAULT 'completed'",
        "parse_error": "ALTER TABLE knowledge_documents ADD COLUMN parse_error TEXT NULL",
    }
    for column, statement in migrations.items():
        if column not in existing_columns:
            await conn.execute(text(statement))


async def ensure_interview_message_columns(conn) -> None:
    """补齐面试消息的题号、维度、请求标识和追问信息，并确保请求唯一索引存在。"""
    result = await conn.execute(text("SHOW COLUMNS FROM interview_messages"))
    existing_columns = {row[0] for row in result.fetchall()}
    migrations = {
        "question_index": "ALTER TABLE interview_messages ADD COLUMN question_index INT NOT NULL DEFAULT 1",
        "dimension": "ALTER TABLE interview_messages ADD COLUMN dimension VARCHAR(160) NULL",
        "request_id": "ALTER TABLE interview_messages ADD COLUMN request_id VARCHAR(36) NULL, ADD UNIQUE KEY uq_interview_message_request (session_id, request_id)",
        "is_followup": "ALTER TABLE interview_messages ADD COLUMN is_followup INT NOT NULL DEFAULT 0",
        "followup_index": "ALTER TABLE interview_messages ADD COLUMN followup_index INT NOT NULL DEFAULT 0",
    }
    for column, statement in migrations.items():
        if column not in existing_columns:
            await conn.execute(text(statement))
    await ensure_interview_request_index(conn)


async def ensure_interview_request_index(conn) -> None:
    """检查会话与请求标识的联合唯一索引，按需替换旧的请求标识单列唯一索引。"""
    rows = (await conn.execute(text("SHOW INDEX FROM interview_messages"))).fetchall()
    indexes: dict[str, list[tuple[int, str, int]]] = {}
    for row in rows:
        indexes.setdefault(str(row[2]), []).append((int(row[3]), str(row[4]), int(row[1])))
    expected_columns = ["session_id", "request_id"]
    for parts in indexes.values():
        ordered = sorted(parts, key=lambda part: part[0])
        if [part[1] for part in ordered] == expected_columns and all(part[2] == 0 for part in ordered):
            return
    request_index = indexes.get("request_id")
    if request_index:
        ordered = sorted(request_index, key=lambda part: part[0])
        if [part[1] for part in ordered] == ["request_id"] and all(part[2] == 0 for part in ordered):
            await conn.execute(text("ALTER TABLE interview_messages DROP INDEX request_id"))
    await conn.execute(text("ALTER TABLE interview_messages ADD UNIQUE KEY uq_interview_message_request (session_id, request_id)"))


async def ensure_interview_session_columns(conn) -> None:
    """补齐面试会话的快照、练习来源和处理租约字段，并为关联查询建立缺失索引。"""
    result = await conn.execute(text("SHOW COLUMNS FROM interview_sessions"))
    existing_columns = {row[0] for row in result.fetchall()}
    migrations = {
        "interview_plan_json": "ALTER TABLE interview_sessions ADD COLUMN interview_plan_json TEXT NULL",
        "mode": "ALTER TABLE interview_sessions ADD COLUMN mode VARCHAR(40) NOT NULL DEFAULT 'training'",
        "interview_type": "ALTER TABLE interview_sessions ADD COLUMN interview_type VARCHAR(40) NOT NULL DEFAULT 'mixed'",
        "resume_id": "ALTER TABLE interview_sessions ADD COLUMN resume_id INT NULL",
        "resume_snapshot_json": "ALTER TABLE interview_sessions ADD COLUMN resume_snapshot_json LONGTEXT NULL",
        "job_description_id": "ALTER TABLE interview_sessions ADD COLUMN job_description_id INT NULL",
        "job_description_snapshot_json": "ALTER TABLE interview_sessions ADD COLUMN job_description_snapshot_json LONGTEXT NULL",
        "practice_context_json": "ALTER TABLE interview_sessions ADD COLUMN practice_context_json LONGTEXT NULL",
        "parent_session_id": "ALTER TABLE interview_sessions ADD COLUMN parent_session_id INT NULL",
        "source_report_id": "ALTER TABLE interview_sessions ADD COLUMN source_report_id INT NULL",
        "source_weakness_key": "ALTER TABLE interview_sessions ADD COLUMN source_weakness_key VARCHAR(255) NULL",
        "session_purpose": "ALTER TABLE interview_sessions ADD COLUMN session_purpose VARCHAR(40) NOT NULL DEFAULT 'full_interview'",
        "comparison_group_id": "ALTER TABLE interview_sessions ADD COLUMN comparison_group_id VARCHAR(64) NULL",
        "processing_request_id": "ALTER TABLE interview_sessions ADD COLUMN processing_request_id VARCHAR(36) NULL",
        "processing_started_at": "ALTER TABLE interview_sessions ADD COLUMN processing_started_at DATETIME NULL",
    }
    for column, statement in migrations.items():
        if column not in existing_columns:
            await conn.execute(text(statement))
    indexes = (await conn.execute(text("SHOW INDEX FROM interview_sessions"))).fetchall()
    existing_indexes = {str(row[2]) for row in indexes}
    required_indexes = {
        "ix_interview_sessions_resume_id": "resume_id",
        "ix_interview_sessions_job_description_id": "job_description_id",
        "ix_interview_sessions_parent_session_id": "parent_session_id",
        "ix_interview_sessions_source_report_id": "source_report_id",
        "ix_interview_sessions_comparison_group_id": "comparison_group_id",
    }
    for index_name, column_name in required_indexes.items():
        if index_name not in existing_indexes:
            await conn.execute(
                text(f"ALTER TABLE interview_sessions ADD INDEX {index_name} ({column_name})")
            )


async def ensure_interview_report_columns(conn) -> None:
    """补齐报告的维度分、引用和生成状态字段，并根据历史会话与评分回填相关数据。"""
    result = await conn.execute(text("SHOW COLUMNS FROM interview_reports"))
    existing_columns = {row[0] for row in result.fetchall()}
    migrations = {
        "dimension_scores_json": "ALTER TABLE interview_reports ADD COLUMN dimension_scores_json TEXT NULL",
        "citations_json": "ALTER TABLE interview_reports ADD COLUMN citations_json TEXT NULL",
    }
    for column, statement in migrations.items():
        if column not in existing_columns:
            await conn.execute(text(statement))
    if "is_final" not in existing_columns:
        await conn.execute(text("ALTER TABLE interview_reports ADD COLUMN is_final BOOL NULL"))
        await conn.execute(
            text(
                "UPDATE interview_reports AS report "
                "JOIN interview_sessions AS session ON session.id = report.session_id "
                "SET report.is_final = (session.status = 'finished' "
                "AND session.session_purpose = 'full_interview' "
                "AND NOT EXISTS (SELECT 1 FROM interview_scores AS newer_score "
                "WHERE newer_score.session_id = report.session_id "
                "AND newer_score.created_at > COALESCE(report.updated_at, report.created_at)) "
                "AND (NOT EXISTS (SELECT 1 FROM question_reviews AS review "
                "WHERE review.report_id = report.id) "
                "OR (SELECT COUNT(*) FROM question_reviews AS review_count "
                "WHERE review_count.report_id = report.id) = "
                "(SELECT COUNT(*) FROM interview_scores AS score_count "
                "WHERE score_count.session_id = report.session_id)))"
            )
        )
        await conn.execute(text("UPDATE interview_reports SET is_final = FALSE WHERE is_final IS NULL"))
        await conn.execute(
            text("ALTER TABLE interview_reports MODIFY COLUMN is_final BOOL NOT NULL DEFAULT TRUE")
        )
    if "generated_from_score_count" not in existing_columns:
        await conn.execute(
            text(
                "ALTER TABLE interview_reports "
                "ADD COLUMN generated_from_score_count INT NOT NULL DEFAULT 0"
            )
        )
        await conn.execute(
            text(
                "UPDATE interview_reports AS report SET generated_from_score_count = "
                "(SELECT COUNT(*) FROM interview_scores AS score "
                "WHERE score.session_id = report.session_id)"
            )
        )


async def ensure_interview_score_columns(conn) -> None:
    """为历史评分表补充兜底标记、兜底原因和知识引用字段。"""
    result = await conn.execute(text("SHOW COLUMNS FROM interview_scores"))
    existing_columns = {row[0] for row in result.fetchall()}
    migrations = {
        "is_fallback": "ALTER TABLE interview_scores ADD COLUMN is_fallback BOOL NOT NULL DEFAULT FALSE",
        "fallback_reason": "ALTER TABLE interview_scores ADD COLUMN fallback_reason TEXT NULL",
        "citations_json": "ALTER TABLE interview_scores ADD COLUMN citations_json TEXT NULL",
    }
    for column, statement in migrations.items():
        if column not in existing_columns:
            await conn.execute(text(statement))


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health/live")
def health_live() -> dict[str, str]:
    """返回进程存活状态，供存活探针检查接口是否可响应。"""
    return {"status": "ok"}


@app.get("/api/health/ready")
async def health_ready() -> JSONResponse:
    """汇总依赖服务的就绪状态，并按检查结果返回对应的响应状态码。"""
    payload, status_code = await readiness_status()
    return JSONResponse(content=payload, status_code=status_code)


@app.get("/api/health")
async def health() -> JSONResponse:
    """提供兼容旧部署检查方式的就绪状态接口。"""
    return await health_ready()


app.include_router(auth.router, prefix="/api")
app.include_router(profiles.router, prefix="/api")
app.include_router(interviews.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(practice.router, prefix="/api")
app.include_router(resumes.router, prefix="/api")
app.include_router(job_descriptions.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(knowledge.router, prefix="/api")
app.include_router(knowledge_async.router, prefix="/api")
