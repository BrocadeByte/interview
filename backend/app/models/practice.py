from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PracticeSession(Base):
    __tablename__ = "practice_sessions"
    __table_args__ = (
        UniqueConstraint("practice_session_id", name="uq_practice_sessions_practice_session"),
        UniqueConstraint("retest_session_id", name="uq_practice_sessions_retest_session"),
        Index("ix_practice_sessions_user_updated", "user_id", "updated_at"),
        Index("ix_practice_sessions_report_created", "source_report_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    source_report_id: Mapped[int] = mapped_column(
        ForeignKey("interview_reports.id"), nullable=False, index=True
    )
    source_session_id: Mapped[int] = mapped_column(
        ForeignKey("interview_sessions.id"), nullable=False, index=True
    )
    source_question_review_id: Mapped[int | None] = mapped_column(
        ForeignKey("question_reviews.id"), nullable=True, index=True
    )
    source_score_id: Mapped[int | None] = mapped_column(
        ForeignKey("interview_scores.id"), nullable=True, index=True
    )
    weakness_key: Mapped[str] = mapped_column(String(255), nullable=False)
    weakness_title: Mapped[str] = mapped_column(String(500), nullable=False)
    target_dimension: Mapped[str] = mapped_column(String(160), nullable=False)
    practice_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    comparison_group_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    practice_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("interview_sessions.id"), nullable=True
    )
    retest_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("interview_sessions.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(40), default="not_started", server_default=text("'not_started'"), nullable=False
    )
    before_score: Mapped[int] = mapped_column(Integer, nullable=False)
    after_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_snapshot_json: Mapped[str] = mapped_column(
        LONGTEXT().with_variant(Text, "sqlite"), nullable=False
    )
    comparison_json: Mapped[str | None] = mapped_column(
        LONGTEXT().with_variant(Text, "sqlite"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
