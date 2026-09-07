from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_JOB_DESCRIPTION_CHARS = 20_000


class JobDescriptionParse(BaseModel):
    raw_text: str = Field(min_length=1, max_length=MAX_JOB_DESCRIPTION_CHARS)
    title: str = Field(min_length=1, max_length=255)
    company_name: str | None = Field(default=None, max_length=255)

    @field_validator("raw_text", "title")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        """去除必填文本首尾空白，并拒绝空白内容。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    @field_validator("company_name")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        """去除可选文本首尾空白，将空文本统一为空值。"""
        if value is None:
            return None
        return value.strip() or None


class ParsedJobDescription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_position: str = Field(min_length=1, max_length=160)
    seniority: str | None = Field(default=None, max_length=80)
    experience_requirements: str | None = Field(default=None, max_length=500)
    must_have_skills: list[str] = Field(default_factory=list, max_length=100)
    nice_to_have_skills: list[str] = Field(default_factory=list, max_length=100)
    responsibilities: list[str] = Field(default_factory=list, max_length=100)
    hard_requirements: list[str] = Field(default_factory=list, max_length=100)
    interview_focus: list[str] = Field(default_factory=list, max_length=50)
    risk_points: list[str] = Field(default_factory=list, max_length=50)


class JobDescriptionRead(BaseModel):
    id: int
    title: str
    company_name: str | None = None
    raw_text: str
    target_position: str
    status: Literal["pending", "parsed", "failed"]
    error_message: str | None = None
    is_active: bool
    parsed: ParsedJobDescription | None = None
    created_at: datetime
    updated_at: datetime
