from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


MAX_INTERVIEW_ANSWER_CHARS = 8_000


class InterviewCreate(BaseModel):
    target_position: str = Field(min_length=1, max_length=160)
    difficulty: str = Field(default="medium", pattern="^(easy|medium|hard)$")
    mode: Literal["training", "mock"] = "training"
    interview_type: Literal[
        "hr", "project_deep_dive", "technical_basics", "system_design", "mixed"
    ] = "mixed"
    resume_id: int | None = Field(default=None, gt=0)
    job_description_id: int | None = Field(default=None, gt=0)
    parent_session_id: int | None = Field(default=None, gt=0)
    source_report_id: int | None = Field(default=None, gt=0)
    source_weakness_key: str | None = Field(default=None, min_length=1, max_length=255)
    session_purpose: Literal["full_interview", "weakness_practice", "retest"] = "full_interview"
    comparison_group_id: str | None = Field(default=None, min_length=1, max_length=64)


class InterviewWarmup(BaseModel):
    target_position: str = Field(min_length=1, max_length=160)


class InterviewAnswer(BaseModel):
    # 8,000 字符足以容纳长篇口述回答，同时防止单轮输入无上限放大后续评分与报告请求。
    answer: str = Field(min_length=1, max_length=MAX_INTERVIEW_ANSWER_CHARS)
    request_id: UUID


class InterviewMessageRead(BaseModel):
    id: int
    role: str
    content: str
    question_index: int = 1
    dimension: str | None = None
    request_id: str | None = None
    is_followup: int = 0
    followup_index: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class InterviewSessionRead(BaseModel):
    id: int
    target_position: str
    difficulty: str
    mode: Literal["training", "mock"] = "training"
    interview_type: Literal[
        "hr", "project_deep_dive", "technical_basics", "system_design", "mixed"
    ] = "mixed"
    status: str
    current_question_index: int
    current_dimension: str | None = None
    current_plan_focus: str | None = None
    total_question_count: int = 8
    resume_id: int | None = None
    job_description_id: int | None = None
    parent_session_id: int | None = None
    source_report_id: int | None = None
    source_weakness_key: str | None = None
    session_purpose: Literal["full_interview", "weakness_practice", "retest"] = "full_interview"
    comparison_group_id: str | None = None
    practice_id: int | None = None
    source_practice_id: int | None = None
    created_at: datetime
    updated_at: datetime
    messages: list[InterviewMessageRead] = []

    model_config = {"from_attributes": True}


class InterviewListItem(BaseModel):
    id: int
    target_position: str
    difficulty: str
    mode: Literal["training", "mock"] = "training"
    interview_type: Literal[
        "hr", "project_deep_dive", "technical_basics", "system_design", "mixed"
    ] = "mixed"
    status: str
    current_question_index: int
    resume_id: int | None = None
    job_description_id: int | None = None
    parent_session_id: int | None = None
    source_report_id: int | None = None
    source_weakness_key: str | None = None
    session_purpose: Literal["full_interview", "weakness_practice", "retest"] = "full_interview"
    comparison_group_id: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
