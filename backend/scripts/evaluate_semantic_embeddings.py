import asyncio
import math

from app.rag.embeddings import embed_texts, embedding_signature


DOCUMENTS = [
    "JWT access token 过期后使用 refresh token 换取新的访问令牌",
    "MySQL 连接池耗尽时检查活跃连接、连接超时配置和慢查询",
    "使用幂等键和数据库唯一约束防止接口重复提交和重复写入",
    "Redis 缓存雪崩、缓存击穿和热点 Key 治理方案",
]

EVALUATION_CASES = [
    ("访问凭证失效后的续期方案", 0),
    ("数据库连接不够用时应该怎样定位", 1),
    ("怎样避免用户重复点击造成数据写入两次", 2),
]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """计算两个等长向量的余弦相似度，任一向量范数为零时返回零。"""
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


async def main() -> None:
    """为评测查询和文档生成向量，统计首位命中率与平均倒数排名并输出结果。"""
    queries = [query for query, _expected in EVALUATION_CASES]
    vectors = await embed_texts(queries + DOCUMENTS)
    query_vectors = vectors[: len(queries)]
    document_vectors = vectors[len(queries) :]

    reciprocal_ranks = []
    top_one_hits = 0
    for (query, expected_index), query_vector in zip(EVALUATION_CASES, query_vectors, strict=True):
        scores = [cosine_similarity(query_vector, vector) for vector in document_vectors]
        ranking = sorted(range(len(scores)), key=lambda index: scores[index], reverse=True)
        rank = ranking.index(expected_index) + 1
        reciprocal_ranks.append(1 / rank)
        top_one_hits += int(rank == 1)
        print(
            {
                "query": query,
                "expected_rank": rank,
                "expected_score": round(scores[expected_index], 4),
                "best_negative_score": round(
                    max(score for index, score in enumerate(scores) if index != expected_index),
                    4,
                ),
            }
        )

    print(
        {
            "embedding_signature": embedding_signature(),
            "dimension": len(vectors[0]),
            "recall_at_1": round(top_one_hits / len(EVALUATION_CASES), 4),
            "mrr": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 4),
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
