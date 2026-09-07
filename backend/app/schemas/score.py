from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.citation import KnowledgeCitation


class InterviewScoreRead(BaseModel):
    id: int
    session_id: int
    question_index: int
    question: str
    answer: str
    dimension: str
    score: int
    sub_scores: dict[str, int]
    reason: str
    weaknesses: list[str]
    suggestions: list[str]
    is_fallback: bool = False
    fallback_reason: str | None = None
    citations: list[KnowledgeCitation] = Field(default_factory=list)
    created_at: datetime
