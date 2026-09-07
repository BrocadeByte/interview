from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class QuestionReviewRead(BaseModel):
    id: int
    report_id: int
    session_id: int
    score_id: int
    question_index: int
    dimension: str
    question: str
    answer: str
    score: int
    sub_scores: dict[str, int] = Field(default_factory=dict)
    deduction_reasons: list[str] = Field(default_factory=list)
    suggested_structure: list[str] = Field(default_factory=list)
    sample_answer: str
    weaknesses: list[str] = Field(default_factory=list)
    weakness_key: str
    practice_seed: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
