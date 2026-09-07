from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    target_position: Mapped[str] = mapped_column(String(160))
    difficulty: Mapped[str] = mapped_column(String(40), default="medium")
    mode: Mapped[str] = mapped_column(
        String(40), default="training", server_default=text("'training'"), nullable=False
    )
    interview_type: Mapped[str] = mapped_column(
        String(40), default="mixed", server_default=text("'mixed'"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(40), default="active")
    current_question_index: Mapped[int] = mapped_column(Integer, default=1)
    interview_plan_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id"), nullable=True, index=True)
    resume_snapshot_json: Mapped[str | None] = mapped_column(
        LONGTEXT().with_variant(Text, "sqlite"), nullable=True
    )
    job_description_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_descriptions.id"), nullable=True, index=True
    )
    job_description_snapshot_json: Mapped[str | None] = mapped_column(
        LONGTEXT().with_variant(Text, "sqlite"), nullable=True
    )
    practice_context_json: Mapped[str | None] = mapped_column(
        LONGTEXT().with_variant(Text, "sqlite"), nullable=True
    )
    parent_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("interview_sessions.id"), nullable=True, index=True
    )
    source_report_id: Mapped[int | None] = mapped_column(
        ForeignKey("interview_reports.id"), nullable=True, index=True
    )
    source_weakness_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    session_purpose: Mapped[str] = mapped_column(
        String(40), default="full_interview", server_default=text("'full_interview'"), nullable=False
    )
    comparison_group_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    processing_request_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="interview_sessions")
    messages: Mapped[list["InterviewMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="InterviewMessage.id"
    )
    memories: Mapped[list["InterviewMemory"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="InterviewMemory.id"
    )


class InterviewMessage(Base):
    __tablename__ = "interview_messages"
    __table_args__ = (UniqueConstraint("session_id", "request_id", name="uq_interview_message_request"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("interview_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    question_index: Mapped[int] = mapped_column(Integer, default=1)
    dimension: Mapped[str | None] = mapped_column(String(160), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    is_followup: Mapped[int] = mapped_column(Integer, default=0)
    followup_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped[InterviewSession] = relationship(back_populates="messages")


class InterviewMemory(Base):
    __tablename__ = "interview_memories"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("interview_sessions.id"), index=True)
    question_index: Mapped[int] = mapped_column(Integer, default=0)
    memory_type: Mapped[str] = mapped_column(String(40), index=True)
    summary: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    session: Mapped[InterviewSession] = relationship(back_populates="memories")
