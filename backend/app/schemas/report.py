from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.citation import KnowledgeCitation


class ReportDimensionScore(BaseModel):
    dimension: str
    score: int
    question_indexes: list[int]
    focus: str
    weaknesses: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class InterviewReportRead(BaseModel):
    id: int | None
    session_id: int
    total_score: int
    summary: str
    strengths: list[str]
    weaknesses: list[str]
    suggestions: list[str]
    dimension_scores: list[ReportDimensionScore] = Field(default_factory=list)
    learning_path: list[str]
    sample_answer: str
    citations: list[KnowledgeCitation] = Field(default_factory=list)
    is_final: bool
    generated_from_score_count: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class InterviewReportListItem(BaseModel):
    id: int
    session_id: int
    target_position: str
    difficulty: str
    total_score: int
    created_at: datetime
    updated_at: datetime
