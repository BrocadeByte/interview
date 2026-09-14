from __future__ import annotations

import asyncio
import math
from importlib.metadata import PackageNotFoundError, version
from time import perf_counter
from types import SimpleNamespace
from typing import Any, Awaitable

from app.core.config import settings
from app.schemas.rag_evaluation import (
    RetrievalEvaluationCase,
    RetrievalEvaluationCaseResult,
    RetrievalEvaluationRequest,
    RetrievalEvaluationResult,
    RetrievalMetricScores,
    RetrievedContextRead,
)
from app.services.knowledge_service import search_knowledge


class RagasUnavailableError(RuntimeError):
    """Ragas 未安装或评审模型没有配置时抛出。"""


def _load_ragas() -> SimpleNamespace:
    """延迟导入 Ragas，使应用的非评测路径不因可选评测依赖失效。"""
    try:
        from openai import AsyncOpenAI
        from ragas import SingleTurnSample
        from ragas.llms import llm_factory
        from ragas.metrics.collections import (
            ContextPrecision,
            ContextRecall,
        )
        # Ragas 0.4.x 的 ID 指标尚未迁移到 collections API，官方示例仍从此处导入。
        from ragas.metrics import IDBasedContextPrecision, IDBasedContextRecall
    except (ImportError, AttributeError) as exc:
        raise RagasUnavailableError(
            "Ragas 评测组件不可用，请安装 backend/requirements.txt 后重启服务"
        ) from exc
    return SimpleNamespace(
        AsyncOpenAI=AsyncOpenAI,
        SingleTurnSample=SingleTurnSample,
        llm_factory=llm_factory,
        ContextPrecision=ContextPrecision,
        ContextRecall=ContextRecall,
        IDBasedContextPrecision=IDBasedContextPrecision,
        IDBasedContextRecall=IDBasedContextRecall,
    )


async def evaluate_retrieval(request: RetrievalEvaluationRequest) -> RetrievalEvaluationResult:
    """执行真实检索链路，用 Ragas 计算语义与标注 ID 指标。"""
    started = perf_counter()
    ragas = _load_ragas()
    needs_llm = any(item.reference for item in request.cases)
    evaluator_model = (settings.ragas_evaluator_model or settings.openai_model).strip() if needs_llm else None
    if needs_llm and (not settings.openai_api_key or not evaluator_model):
        raise RagasUnavailableError("语义评测需要配置 OPENAI_API_KEY 和评审模型")

    client = None
    context_precision = None
    context_recall = None
    if needs_llm:
        try:
            client = ragas.AsyncOpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_api_base,
                timeout=max(1, settings.ragas_evaluation_timeout_seconds),
                max_retries=settings.llm_max_retries,
            )
            judge = ragas.llm_factory(evaluator_model, client=client)
            context_precision = ragas.ContextPrecision(llm=judge)
            context_recall = ragas.ContextRecall(llm=judge)
        except Exception as exc:
            if client is not None:
                await client.close()
            raise RagasUnavailableError(f"Ragas 评审模型初始化失败: {type(exc).__name__}: {exc}") from exc

    scorers = SimpleNamespace(
        context_precision=context_precision,
        context_recall=context_recall,
        id_context_precision=ragas.IDBasedContextPrecision(),
        id_context_recall=ragas.IDBasedContextRecall(),
    )
    semaphore = asyncio.Semaphore(max(1, settings.ragas_max_concurrency))

    async def run_case(index: int, item: RetrievalEvaluationCase) -> RetrievalEvaluationCaseResult:
        async with semaphore:
            return await _evaluate_case(index, item, request.top_k, ragas, scorers)

    try:
        cases = await asyncio.gather(*(run_case(index, item) for index, item in enumerate(request.cases, start=1)))
    finally:
        if client is not None:
            await client.close()

    return RetrievalEvaluationResult(
        framework="ragas",
        framework_version=_ragas_version(),
        evaluator_model=evaluator_model,
        top_k=request.top_k,
        case_count=len(cases),
        metrics=_average_metrics(cases),
        cases=cases,
        duration_ms=_elapsed_ms(started),
    )


async def _evaluate_case(
    index: int,
    item: RetrievalEvaluationCase,
    top_k: int,
    ragas: SimpleNamespace,
    scorers: SimpleNamespace,
) -> RetrievalEvaluationCaseResult:
    started = perf_counter()
    chunks = await search_knowledge(
        item.query,
        limit=top_k,
        target_position=item.target_position,
        categories=item.categories or None,
        include_general=item.include_general,
    )
    retrieved_document_ids = _unique_document_ids(chunks)
    contexts = [str(chunk.get("text") or chunk.get("content") or "") for chunk in chunks]
    scores = RetrievalMetricScores()
    errors: dict[str, str] = {}

    if item.reference and not contexts:
        scores.context_precision = 0.0
        scores.context_recall = 0.0
    elif item.reference:
        semantic_scores = await asyncio.gather(
            _safe_metric_score(
                "context_precision",
                scorers.context_precision.ascore(
                    user_input=item.query,
                    reference=item.reference,
                    retrieved_contexts=contexts,
                ),
            ),
            _safe_metric_score(
                "context_recall",
                scorers.context_recall.ascore(
                    user_input=item.query,
                    reference=item.reference,
                    retrieved_contexts=contexts,
                ),
            ),
        )
        for metric_name, score, error in semantic_scores:
            setattr(scores, metric_name, score)
            if error:
                errors[metric_name] = error

    if item.reference_document_ids and not retrieved_document_ids:
        scores.id_context_precision = 0.0
        scores.id_context_recall = 0.0
        scores.hit_rate = 0.0
        scores.mrr = 0.0
    elif item.reference_document_ids:
        sample = ragas.SingleTurnSample(
            retrieved_context_ids=retrieved_document_ids,
            reference_context_ids=item.reference_document_ids,
        )
        id_scores = await asyncio.gather(
            _safe_metric_score(
                "id_context_precision",
                scorers.id_context_precision.single_turn_ascore(sample),
            ),
            _safe_metric_score(
                "id_context_recall",
                scorers.id_context_recall.single_turn_ascore(sample),
            ),
        )
        for metric_name, score, error in id_scores:
            setattr(scores, metric_name, score)
            if error:
                errors[metric_name] = error
        scores.hit_rate = float(any(doc_id in item.reference_document_ids for doc_id in retrieved_document_ids))
        scores.mrr = _reciprocal_rank(retrieved_document_ids, item.reference_document_ids)

    return RetrievalEvaluationCaseResult(
        case_id=item.case_id or f"case-{index}",
        query=item.query,
        reference=item.reference,
        reference_document_ids=item.reference_document_ids,
        metrics=scores,
        retrieved_contexts=_retrieved_contexts(chunks, set(item.reference_document_ids)),
        metric_errors=errors,
        duration_ms=_elapsed_ms(started),
    )


async def _safe_metric_score(
    metric_name: str,
    awaitable: Awaitable[Any],
) -> tuple[str, float | None, str | None]:
    try:
        async with asyncio.timeout(max(1, settings.ragas_evaluation_timeout_seconds)):
            raw = await awaitable
        score = _normalized_score(raw)
        if score is None:
            return metric_name, None, "Ragas 未返回有效数值"
        return metric_name, score, None
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        return metric_name, None, message[:500]


def _normalized_score(raw: Any) -> float | None:
    """兼容 collections API 的 MetricResult 与 ID 指标的 float 返回值。"""
    value = getattr(raw, "value", raw)
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(score):
        return None
    return round(min(1.0, max(0.0, score)), 6)


def _unique_document_ids(chunks: list[dict]) -> list[int]:
    return list(dict.fromkeys(int(chunk.get("document_id") or 0) for chunk in chunks if chunk.get("document_id")))


def _reciprocal_rank(retrieved_ids: list[int], reference_ids: list[int]) -> float:
    expected = set(reference_ids)
    for rank, document_id in enumerate(retrieved_ids, start=1):
        if document_id in expected:
            return round(1 / rank, 6)
    return 0.0


def _retrieved_contexts(chunks: list[dict], reference_ids: set[int]) -> list[RetrievedContextRead]:
    results: list[RetrievedContextRead] = []
    has_reference_ids = bool(reference_ids)
    for rank, chunk in enumerate(chunks, start=1):
        document_id = int(chunk.get("document_id") or 0)
        if document_id <= 0:
            continue
        context_chunks = chunk.get("_context_chunks") or []
        primary = next((part for part in context_chunks if part.get("is_primary")), None) or {}
        text = str(chunk.get("text") or chunk.get("content") or "").strip()
        retrieval_score = float(chunk.get("_rerank_score") or chunk.get("_rrf_score") or 0.0)
        results.append(
            RetrievedContextRead(
                rank=rank,
                document_id=document_id,
                title=str(chunk.get("title") or "Untitled"),
                chunk_id=str(primary.get("chunk_id") or chunk.get("point_id") or ""),
                chunk_index=int(primary.get("chunk_index") or chunk.get("chunk_index") or 0),
                content_preview=text[:500],
                retrieval_score=round(retrieval_score, 6),
                retrieval_routes=[str(route) for route in chunk.get("_retrieval_routes") or []],
                relevant=document_id in reference_ids if has_reference_ids else None,
            )
        )
    return results


def _average_metrics(cases: list[RetrievalEvaluationCaseResult]) -> RetrievalMetricScores:
    fields = RetrievalMetricScores.model_fields
    averages: dict[str, float | None] = {}
    for field_name in fields:
        values = [getattr(item.metrics, field_name) for item in cases]
        available = [value for value in values if value is not None]
        averages[field_name] = round(sum(available) / len(available), 6) if available else None
    return RetrievalMetricScores(**averages)


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _ragas_version() -> str:
    try:
        return version("ragas")
    except PackageNotFoundError:
        return "unknown"
