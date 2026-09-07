from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class QuestionReview(Base):
    __tablename__ = "question_reviews"
    __table_args__ = (
        UniqueConstraint("report_id", "score_id", name="uq_question_reviews_report_score"),
        Index("ix_question_reviews_report_question", "report_id", "question_index", "id"),
        Index("ix_question_reviews_session_question", "session_id", "question_index", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("interview_reports.id"), nullable=False)
    session_id: Mapped[int] = mapped_column(ForeignKey("interview_sessions.id"), nullable=False)
    score_id: Mapped[int] = mapped_column(ForeignKey("interview_scores.id"), nullable=False, index=True)
    question_index: Mapped[int] = mapped_column(Integer, nullable=False)
    dimension: Mapped[str] = mapped_column(String(160), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    sub_scores_json: Mapped[str] = mapped_column(Text, nullable=False)
    deduction_reasons_json: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_structure_json: Mapped[str] = mapped_column(Text, nullable=False)
    sample_answer: Mapped[str] = mapped_column(Text, nullable=False)
    weaknesses_json: Mapped[str] = mapped_column(Text, nullable=False)
    weakness_key: Mapped[str] = mapped_column(String(255), nullable=False)
    practice_seed_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
