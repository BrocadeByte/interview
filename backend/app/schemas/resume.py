from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


MAX_RESUME_TEXT_CHARS = 20_000


class ResumePaste(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=MAX_RESUME_TEXT_CHARS)

    @field_validator("title", "content")
    @classmethod
    def strip_non_empty_text(cls, value: str) -> str:
        """清理粘贴简历字段的首尾空白，并拒绝空白内容。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class ResumeProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    age: StrictInt | None = Field(default=None, ge=0, le=100)
    education: str | None = Field(default=None, max_length=120)
    major: str | None = Field(default=None, max_length=120)
    experience_years: StrictInt | None = Field(default=None, ge=0, le=60)
    target_position: str | None = Field(default=None, max_length=160)
    target_city: str | None = Field(default=None, max_length=120)
    expected_salary: str | None = Field(default=None, max_length=80)
    skills: str | None = Field(default=None, max_length=4_000)
    projects: str | None = Field(default=None, max_length=12_000)
    self_evaluation: str | None = Field(default=None, max_length=2_000)

    @field_validator(
        "education",
        "major",
        "target_position",
        "target_city",
        "expected_salary",
        "skills",
        "projects",
        "self_evaluation",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """清理简历画像补丁中的可选文本，并将空文本转换为空值。"""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ResumeProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    role: str | None = Field(default=None, max_length=255)
    description: str = Field(default="", max_length=4_000)
    tech_stack: list[str] = Field(default_factory=list, max_length=100)
    highlights: list[str] = Field(default_factory=list, max_length=100)


class ResumeWorkExperience(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str = Field(min_length=1, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    period: str | None = Field(default=None, max_length=120)
    description: str = Field(default="", max_length=4_000)
    highlights: list[str] = Field(default_factory=list, max_length=100)


class ParsedResume(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(default_factory=list, max_length=200)
    education: list[str] = Field(default_factory=list, max_length=50)
    projects: list[ResumeProject] = Field(default_factory=list, max_length=50)
    work_experience: list[ResumeWorkExperience] = Field(default_factory=list, max_length=50)
    experience_summary: str = Field(default="", max_length=4_000)


class ResumeParseOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parsed: ParsedResume
    profile_patch: ResumeProfilePatch


class ResumeRead(BaseModel):
    id: int
    title: str
    source_type: Literal["upload", "paste"]
    file_name: str | None = None
    file_type: str | None = None
    raw_text: str
    status: Literal["pending", "parsed", "failed"]
    error_message: str | None = None
    is_active: bool
    parsed: ParsedResume | None = None
    profile_patch: ResumeProfilePatch | None = None
    created_at: datetime
    updated_at: datetime
