from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


AnalyticsEventName = Literal[
    "resume_uploaded",
    "resume_pasted",
    "jd_pasted",
    "profile_auto_generated",
    "profile_applied",
    "interview_created",
    "interview_started",
    "interview_finished",
    "report_viewed",
    "question_review_expanded",
    "practice_created",
    "practice_finished",
    "retest_finished",
    "comparison_viewed",
]

ClientAnalyticsEventName = Literal["profile_applied", "question_review_expanded"]


class ClientAnalyticsEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_name: ClientAnalyticsEventName
    client_event_id: UUID
    report_id: int | None = Field(default=None, gt=0)
    question_review_id: int | None = Field(default=None, gt=0)
    resume_id: int | None = Field(default=None, gt=0)
    job_description_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_event_subject(self) -> "ClientAnalyticsEventCreate":
        """按事件类型校验报告与逐题复盘标识，拒绝缺少关联信息或携带无关字段的事件。"""
        if self.event_name == "question_review_expanded":
            if self.report_id is None or self.question_review_id is None:
                raise ValueError(
                    "question_review_expanded requires report_id and question_review_id"
                )
        elif self.report_id is not None or self.question_review_id is not None:
            raise ValueError("profile_applied does not accept report review fields")
        return self


class ConversionMetric(BaseModel):
    denominator: int = Field(ge=0)
    numerator: int = Field(ge=0)
    rate: float = Field(ge=0, le=1)


class FirstExperienceFunnel(BaseModel):
    registered_users: int = Field(ge=0)
    resume_provided_users: int = Field(ge=0)
    jd_pasted_users: int = Field(ge=0)
    profile_auto_generated_users: int = Field(ge=0)
    profile_applied_users: int = Field(ge=0)
    interview_created_users: int = Field(ge=0)
    first_interview_started_users: int = Field(ge=0)


class AnalyticsMetricsRead(BaseModel):
    period_start: date
    period_end: date
    first_experience_funnel: FirstExperienceFunnel
    registration_to_first_interview_start: ConversionMetric
    report_to_practice: ConversionMetric
    seven_day_repractice: ConversionMetric
    event_counts: dict[str, int]
    definitions: dict[str, str]
    generated_at: datetime
