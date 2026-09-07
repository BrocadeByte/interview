from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


PracticeStatus = Literal["not_started", "practicing", "ready_for_retest", "completed"]


class PracticeFromReportCreate(BaseModel):
    report_id: int = Field(gt=0)
    weakness_key: str = Field(min_length=1, max_length=255)
    weakness_title: str = Field(min_length=1, max_length=500)


class PracticeFromQuestionReviewCreate(PracticeFromReportCreate):
    question_review_id: int = Field(gt=0)
    score_id: int = Field(gt=0)
    question_index: int = Field(gt=0)
    practice_mode: Literal["repeat_question", "similar_question"]


class PracticeCreationRead(BaseModel):
    id: int
    status: PracticeStatus
    practice_session_id: int | None = None
    retest_session_id: int | None = None

    model_config = {"from_attributes": True}


class PracticeListItem(PracticeCreationRead):
    source_report_id: int
    source_session_id: int
    source_question_review_id: int | None = None
    weakness_key: str
    weakness_title: str
    target_dimension: str | None = None
    before_score: int | None = None
    after_score: int | None = None
    created_at: datetime
    updated_at: datetime


class PracticeComparisonSession(BaseModel):
    session_id: int
    score: int
    weaknesses: list[str] = Field(default_factory=list)
    answer: str = ""
    answer_structure: list[str] = Field(default_factory=list)
    sub_scores: dict[str, int] = Field(default_factory=dict)


class PracticeComparisonDelta(BaseModel):
    score: int = 0
    resolved_weaknesses: list[str] = Field(default_factory=list)
    remaining_weaknesses: list[str] = Field(default_factory=list)
    sub_scores: dict[str, int] = Field(default_factory=dict)


class PracticeNextWeakness(BaseModel):
    key: str
    title: str


class PracticeComparisonRead(BaseModel):
    practice_id: int
    status: PracticeStatus
    source_report_id: int
    practice_session_id: int | None = None
    retest_session_id: int | None = None
    weakness_title: str
    before: PracticeComparisonSession
    after: PracticeComparisonSession | None = None
    delta: PracticeComparisonDelta
    summary: str
    next_weakness: PracticeNextWeakness | None = None
