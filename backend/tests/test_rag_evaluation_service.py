import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.rag_evaluation import (  # noqa: E402
    RetrievalEvaluationCase,
    RetrievalEvaluationCaseResult,
    RetrievalMetricScores,
)
from app.services.rag_evaluation_service import (  # noqa: E402
    _average_metrics,
    _evaluate_case,
    _normalized_score,
    _reciprocal_rank,
    _retrieved_contexts,
    _unique_document_ids,
)


class RetrievalEvaluationHelpersTest(unittest.TestCase):
    def test_case_requires_at_least_one_reference_type(self) -> None:
        with self.assertRaises(ValueError):
            RetrievalEvaluationCase(query="HashMap 如何扩容？")

    def test_case_normalizes_and_deduplicates_labels(self) -> None:
        item = RetrievalEvaluationCase(
            query="  HashMap 如何扩容？  ",
            reference_document_ids=[3, 3, 8],
            categories=["岗位能力", " ", "岗位能力"],
        )
        self.assertEqual(item.query, "HashMap 如何扩容？")
        self.assertEqual(item.reference_document_ids, [3, 8])
        self.assertEqual(item.categories, ["岗位能力"])

    def test_document_metrics_helpers_preserve_rank(self) -> None:
        chunks = [{"document_id": 9}, {"document_id": 9}, {"document_id": 4}]
        self.assertEqual(_unique_document_ids(chunks), [9, 4])
        self.assertEqual(_reciprocal_rank([9, 4, 7], [4, 8]), 0.5)
        self.assertEqual(_reciprocal_rank([9, 4], [8]), 0.0)

    def test_normalized_score_accepts_metric_result_and_rejects_nan(self) -> None:
        self.assertEqual(_normalized_score(SimpleNamespace(value=0.81234567)), 0.812346)
        self.assertIsNone(_normalized_score(math.nan))

    def test_retrieved_contexts_marks_human_labels(self) -> None:
        result = _retrieved_contexts(
            [
                {
                    "document_id": 4,
                    "title": "HashMap",
                    "point_id": "chunk-4-0",
                    "chunk_index": 0,
                    "text": "content",
                    "_rerank_score": 0.9,
                    "_retrieval_routes": ["vector", "bm25"],
                }
            ],
            {4},
        )
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].relevant)
        self.assertEqual(result[0].retrieval_routes, ["vector", "bm25"])

    def test_average_metrics_ignores_unavailable_values(self) -> None:
        cases = [
            RetrievalEvaluationCaseResult(
                case_id="1",
                query="q1",
                metrics=RetrievalMetricScores(context_precision=1.0, hit_rate=1.0),
                duration_ms=1,
            ),
            RetrievalEvaluationCaseResult(
                case_id="2",
                query="q2",
                metrics=RetrievalMetricScores(context_precision=0.5),
                duration_ms=1,
            ),
        ]
        scores = _average_metrics(cases)
        self.assertEqual(scores.context_precision, 0.75)
        self.assertEqual(scores.hit_rate, 1.0)
        self.assertIsNone(scores.context_recall)


class RetrievalEvaluationEmptyResultTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_retrieval_scores_zero_without_calling_ragas(self) -> None:
        item = RetrievalEvaluationCase(
            query="HashMap 如何扩容？",
            reference="HashMap 在超过阈值时扩容。",
            reference_document_ids=[4],
        )
        scorers = SimpleNamespace(
            context_precision=AsyncMock(),
            context_recall=AsyncMock(),
            id_context_precision=AsyncMock(),
            id_context_recall=AsyncMock(),
        )
        with patch("app.services.rag_evaluation_service.search_knowledge", AsyncMock(return_value=[])):
            result = await _evaluate_case(1, item, 5, SimpleNamespace(), scorers)

        self.assertEqual(result.metrics.context_precision, 0.0)
        self.assertEqual(result.metrics.context_recall, 0.0)
        self.assertEqual(result.metrics.id_context_precision, 0.0)
        self.assertEqual(result.metrics.id_context_recall, 0.0)
        self.assertEqual(result.metrics.hit_rate, 0.0)
        self.assertEqual(result.metrics.mrr, 0.0)


if __name__ == "__main__":
    unittest.main()
