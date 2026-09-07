from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProfileBase(BaseModel):
    age: int | None = Field(default=None, ge=0, le=100)
    education: str | None = None
    major: str | None = None
    experience_years: int | None = Field(default=None, ge=0, le=60)
    target_position: str | None = None
    target_city: str | None = None
    expected_salary: str | None = None
    skills: str | None = None
    projects: str | None = None
    self_evaluation: str | None = None


class ProfileUpdate(ProfileBase):
    education: str | None = Field(default=None, max_length=120)
    major: str | None = Field(default=None, max_length=120)
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
        """清理画像可选文本的首尾空白，并将空文本转换为空值。"""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class AutoProfileGenerate(BaseModel):
    resume_id: int | None = Field(default=None, gt=0)
    job_description_id: int | None = Field(default=None, gt=0)
    target_position: str | None = Field(default=None, max_length=160)

    @field_validator("target_position")
    @classmethod
    def normalize_target_position(cls, value: str | None) -> str | None:
        """清理自动画像请求中的目标岗位，将空白岗位视为未指定。"""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


ProfileFieldSource = Literal["manual", "resume", "job_description", "request"]


class AutoProfileDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_patch: ProfileUpdate
    completeness: int = Field(ge=0, le=100)
    auto_summary: str | None = Field(default=None, max_length=2_000)
    warnings: list[str] = Field(default_factory=list, max_length=50)
    missing_fields: list[str] = Field(default_factory=list, max_length=20)
    field_sources: dict[str, ProfileFieldSource] = Field(default_factory=dict)


class ProfileRead(ProfileBase):
    id: int
    user_id: int

    model_config = {"from_attributes": True}
