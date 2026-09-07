from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AnalyticsEvent(Base):
    """保存在应用数据库中的产品使用事件记录。"""

    __tablename__ = "analytics_events"
    __table_args__ = (
        UniqueConstraint("deduplication_key", name="uq_analytics_events_deduplication_key"),
        Index("ix_analytics_events_name_occurred", "event_name", "occurred_at"),
        Index("ix_analytics_events_user_occurred", "user_id", "occurred_at"),
        Index("ix_analytics_events_report_name", "report_id", "event_name"),
        Index("ix_analytics_events_practice_name", "practice_id", "event_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    event_name: Mapped[str] = mapped_column(String(64), nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(255), nullable=False)
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    report_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    practice_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    question_review_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resume_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_description_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    properties_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
