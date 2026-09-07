import re
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job_description import JobDescription
from app.models.profile import UserProfile
from app.models.resume import Resume
from app.schemas.job_description import ParsedJobDescription
from app.schemas.profile import (
    AutoProfileDraft,
    AutoProfileGenerate,
    ProfileFieldSource,
    ProfileRead,
    ProfileUpdate,
)
from app.schemas.resume import ParsedResume, ResumeProfilePatch
from app.services.analytics_service import record_analytics_event_safely
from app.services.job_description_service import load_parsed_job_description
from app.services.resume_service import load_json_object


PROFILE_COMPLETENESS_WEIGHTS = {
    "target_position": 20,
    "skills": 20,
    "projects": 20,
    "experience_years": 10,
    "education": 10,
    "major": 5,
    "self_evaluation": 10,
    "target_city": 5,
}
PROFILE_FIELD_LABELS = {
    "target_position": "目标岗位",
    "skills": "技能栈",
    "projects": "项目经历",
    "experience_years": "工作/项目年限",
    "education": "学历",
    "major": "专业",
    "self_evaluation": "自我评价",
    "target_city": "目标城市",
}
LOW_COMPLETENESS_THRESHOLD = 70


async def generate_auto_profile_draft(
    db: AsyncSession,
    *,
    user_id: int,
    payload: AutoProfileGenerate,
) -> AutoProfileDraft:
    """汇总当前画像、简历和岗位快照，生成不直接落库的可确认草稿。"""
    profile = await get_user_profile(db, user_id)
    existing_profile = (
        ProfileRead.model_validate(profile).model_dump(exclude={"id", "user_id"})
        if profile
        else None
    )

    resume_patch: ResumeProfilePatch | None = None
    parsed_resume: ParsedResume | None = None
    if payload.resume_id is not None:
        resume = await get_owned_resume(db, payload.resume_id, user_id)
        if resume.status != "parsed" or not resume.profile_patch_json:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Resume has no parsed profile draft",
            )
        try:
            resume_patch = ResumeProfilePatch.model_validate(
                load_json_object(resume.profile_patch_json)
            )
            parsed_data = load_json_object(resume.parsed_json)
            parsed_resume = (
                ParsedResume.model_validate(parsed_data) if parsed_data is not None else None
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Stored resume parsing result is invalid",
            ) from exc

    parsed_job_description: ParsedJobDescription | None = None
    if payload.job_description_id is not None:
        job_description = await get_owned_job_description(
            db,
            payload.job_description_id,
            user_id,
        )
        if job_description.status != "parsed" or not job_description.parsed_json:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Job description has no parsed result",
            )
        try:
            parsed_job_description = load_parsed_job_description(
                job_description.parsed_json
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Stored job description parsing result is invalid",
            ) from exc

    draft = build_auto_profile_draft(
        existing_profile=existing_profile,
        resume_patch=resume_patch,
        parsed_resume=parsed_resume,
        parsed_job_description=parsed_job_description,
        requested_target_position=payload.target_position,
    )
    await record_analytics_event_safely(
        db,
        event_name="profile_auto_generated",
        user_id=user_id,
        resume_id=payload.resume_id,
        job_description_id=payload.job_description_id,
        properties={"completeness": draft.completeness},
    )
    await db.commit()
    return draft


async def get_or_create_profile(db: AsyncSession, user_id: int) -> UserProfile:
    """返回用户画像；首次访问时创建空画像。"""
    profile = await get_user_profile(db, user_id)
    if profile is None:
        profile = UserProfile(user_id=user_id)
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
    return profile


async def update_user_profile(
    db: AsyncSession,
    *,
    user_id: int,
    payload: ProfileUpdate,
) -> UserProfile:
    """只更新请求中显式提供的画像字段，并提交修改。"""
    profile = await get_user_profile(db, user_id) or UserProfile(user_id=user_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


async def get_user_profile(db: AsyncSession, user_id: int) -> UserProfile | None:
    """按用户 ID 查询求职画像。"""
    return await db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))


async def get_owned_resume(db: AsyncSession, resume_id: int, user_id: int) -> Resume:
    """读取用户拥有的简历，隐藏其他用户记录的存在。"""
    resume = await db.scalar(
        select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id)
    )
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")
    return resume


async def get_owned_job_description(
    db: AsyncSession,
    job_description_id: int,
    user_id: int,
) -> JobDescription:
    """读取用户拥有的岗位描述，隐藏其他用户记录的存在。"""
    job_description = await db.scalar(
        select(JobDescription).where(
            JobDescription.id == job_description_id,
            JobDescription.user_id == user_id,
        )
    )
    if job_description is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job description not found",
        )
    return job_description


def build_auto_profile_draft(
    *,
    existing_profile: Mapping[str, Any] | None,
    resume_patch: ResumeProfilePatch | None,
    parsed_resume: ParsedResume | None,
    parsed_job_description: ParsedJobDescription | None,
    requested_target_position: str | None,
) -> AutoProfileDraft:
    """按明确优先级合并可信快照，生成可审核但不直接持久化的画像草稿。"""
    draft = ProfileUpdate.model_validate(dict(existing_profile or {}))
    values = draft.model_dump()
    sources: dict[str, ProfileFieldSource] = {
        field: "manual" for field, value in values.items() if _has_value(value)
    }

    if resume_patch is not None:
        for field, value in resume_patch.model_dump(exclude_none=True).items():
            if _has_value(value):
                values[field] = value
                sources[field] = "resume"

    if parsed_job_description is not None:
        values["target_position"] = parsed_job_description.target_position
        sources["target_position"] = "job_description"

    if requested_target_position:
        values["target_position"] = requested_target_position
        sources["target_position"] = "request"

    merged = ProfileUpdate.model_validate(values)
    completeness, missing_fields = calculate_profile_completeness(merged)
    warnings = _build_warnings(
        completeness=completeness,
        missing_fields=missing_fields,
        merged=merged,
        parsed_resume=parsed_resume,
        parsed_job_description=parsed_job_description,
        has_resume=resume_patch is not None or parsed_resume is not None,
    )
    return AutoProfileDraft(
        profile_patch=merged,
        completeness=completeness,
        auto_summary=_build_auto_summary(merged, parsed_resume),
        warnings=warnings,
        missing_fields=missing_fields,
        field_sources=sources,
    )


def calculate_profile_completeness(profile: ProfileUpdate) -> tuple[int, list[str]]:
    """按核心字段权重计算画像完整度和缺失字段。"""
    values = profile.model_dump()
    score = sum(
        weight
        for field, weight in PROFILE_COMPLETENESS_WEIGHTS.items()
        if _has_value(values.get(field))
    )
    missing_fields = [
        field for field in PROFILE_COMPLETENESS_WEIGHTS if not _has_value(values.get(field))
    ]
    return score, missing_fields


def _build_warnings(
    *,
    completeness: int,
    missing_fields: list[str],
    merged: ProfileUpdate,
    parsed_resume: ParsedResume | None,
    parsed_job_description: ParsedJobDescription | None,
    has_resume: bool,
) -> list[str]:
    """根据画像完整度、简历有无及岗位必备技能匹配情况生成去重后的补充提示。"""
    warnings: list[str] = []
    if completeness < LOW_COMPLETENESS_THRESHOLD:
        labels = "、".join(PROFILE_FIELD_LABELS[field] for field in missing_fields)
        warnings.append(
            f"画像完整度低于 {LOW_COMPLETENESS_THRESHOLD}%，建议补充：{labels}。"
        )
    if not has_resume:
        warnings.append("未提供已解析简历，项目深挖将主要依据现有手动画像。")

    if parsed_job_description is not None:
        candidate_skill_text = " ".join(
            [merged.skills or "", *(parsed_resume.skills if parsed_resume else [])]
        )
        missing_required_skills = [
            skill
            for skill in parsed_job_description.must_have_skills
            if not _skill_is_mentioned(skill, candidate_skill_text)
        ]
        if missing_required_skills:
            skills = "、".join(missing_required_skills[:8])
            warnings.append(
                f"简历和现有画像中未明确体现 JD 硬性技能：{skills}；请仅按真实经历补充。"
            )
        warnings.extend(
            f"JD 风险提示：{risk}" for risk in parsed_job_description.risk_points[:8]
        )
    return _deduplicate(warnings)


def _build_auto_summary(profile: ProfileUpdate, parsed_resume: ParsedResume | None) -> str | None:
    """汇总目标岗位、经验年限、技能和项目数量，生成简短画像摘要。"""
    parts: list[str] = []
    if profile.target_position:
        parts.append(f"目标岗位：{profile.target_position}")
    if profile.experience_years is not None:
        parts.append(f"经验：{profile.experience_years} 年")
    if profile.skills:
        skill_summary = re.sub(r"\s+", " ", profile.skills).strip()
        parts.append(f"技能：{skill_summary[:240]}")
    project_count = len(parsed_resume.projects) if parsed_resume else 0
    if project_count:
        parts.append(f"简历项目：{project_count} 个")
    elif profile.projects:
        parts.append("已包含项目经历")
    return "；".join(parts) or None


def _skill_is_mentioned(required_skill: str, candidate_text: str) -> bool:
    """规范化技能和候选人文本后，用包含关系判断必备技能是否被提及。"""
    required = _normalize_skill(required_skill)
    candidate = _normalize_skill(candidate_text)
    return bool(required) and required in candidate


def _normalize_skill(value: str) -> str:
    """统一大小写并只保留字母、数字和汉字，便于比较技能文本。"""
    return "".join(char for char in value.casefold() if char.isalnum() or "\u4e00" <= char <= "\u9fff")


def _has_value(value: Any) -> bool:
    """判断画像字段是否已填写，将空值与仅含空白的字符串视为未填写。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _deduplicate(items: list[str]) -> list[str]:
    """过滤空白文本，并按首次出现顺序去除完全相同的条目。"""
    return list(dict.fromkeys(item for item in items if item.strip()))
