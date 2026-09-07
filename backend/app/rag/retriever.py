import asyncio
import logging
import math
import re
from collections import Counter
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from app.core.config import settings
from app.rag.embeddings import embed_text, embed_texts, embedding_signature, validate_embedding_settings
from app.rag.reranker import RerankUnavailableError, rerank_documents


logger = logging.getLogger(__name__)
client = AsyncQdrantClient(url=settings.qdrant_url)

RRF_K = 60
MAX_CONTEXT_CHARS_PER_CHUNK = 900
MAX_PERSISTENT_CANDIDATES = 500
_WORD_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def active_alias_name() -> str:
    """生成指向当前可用知识集合的稳定别名。"""
    return f"{settings.qdrant_collection_name}__active"


async def close_retriever_client() -> None:
    """关闭异步向量检索客户端，释放连接资源。"""
    await client.close()


async def warm_retriever() -> None:
    """预先确认知识集合可用，失败时记录警告以避免中断应用启动。"""
    try:
        await ensure_collection()
    except Exception as exc:
        logger.warning("rag.qdrant.warmup.failed url=%s error=%r", settings.qdrant_url, exc)


def _qdrant_point_id(chunk: dict) -> str:
    """根据分块标识或文档与分块序号生成稳定的向量点 UUID。"""
    raw_id = str(chunk.get("point_id") or f"{chunk.get('document_id', 0)}-{chunk.get('chunk_index', 0)}")
    return str(uuid5(NAMESPACE_URL, raw_id))


def normalize_scope_value(value: str | None) -> str:
    """合并连续空白并统一大小写，规范化检索范围的字段值。"""
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _prepare_payload(chunk: dict) -> dict:
    """复制分块数据并补齐岗位、分类、状态、索引版本与向量配置签名。"""
    payload = dict(chunk)
    payload["target_position_key"] = normalize_scope_value(payload.get("target_position")) or "general"
    payload["category_key"] = normalize_scope_value(payload.get("category"))
    payload["document_status"] = str(payload.get("document_status") or "ready")
    payload["index_version"] = int(payload.get("index_version") or 1)
    payload["embedding_signature"] = embedding_signature()
    return payload


async def _aliases() -> list:
    """读取向量数据库中已注册的集合别名。"""
    return list((await client.get_aliases()).aliases)


async def get_active_collection_target() -> str:
    """返回活动别名指向的真实集合名称，无别名时使用配置中的集合。"""
    alias = active_alias_name()
    for item in await _aliases():
        if item.alias_name == alias:
            return str(item.collection_name)
    return settings.qdrant_collection_name


async def get_active_collection_name() -> str:
    """优先返回可用于查询的活动别名，无别名时使用配置中的集合名称。"""
    alias = active_alias_name()
    for item in await _aliases():
        if item.alias_name == alias:
            return alias
    return settings.qdrant_collection_name


async def _validate_collection(collection_name: str) -> None:
    """检查集合的向量维度是否匹配当前配置，不匹配时要求重建索引。"""
    info = await client.get_collection(collection_name=collection_name)
    vectors = info.config.params.vectors
    actual_size = getattr(vectors, "size", None)
    if actual_size != settings.embedding_dim:
        raise ValueError(
            f"Qdrant vector size mismatch: expected {settings.embedding_dim}, got {actual_size}; reindex required"
        )


async def ensure_collection() -> None:
    """确保活动别名或旧版知识集合可用。"""
    collections = (await client.get_collections()).collections
    aliases = await _aliases()
    alias = active_alias_name()
    if any(item.alias_name == alias for item in aliases):
        await _validate_collection(alias)
        return

    base = settings.qdrant_collection_name
    if any(collection.name == base for collection in collections):
        await _validate_collection(base)
        return
    await create_collection(base)


async def create_collection(collection_name: str) -> None:
    """按配置的向量维度和余弦距离创建知识集合。"""
    logger.info("rag.qdrant.collection.create name=%s vector_size=%s", collection_name, settings.embedding_dim)
    await client.create_collection(
        collection_name=collection_name,
        vectors_config=qmodels.VectorParams(size=settings.embedding_dim, distance=qmodels.Distance.COSINE),
    )


async def delete_collection(collection_name: str) -> None:
    """删除指定的向量集合及其中的数据。"""
    await client.delete_collection(collection_name=collection_name)


async def switch_active_collection(target_collection: str) -> str:
    """将稳定的活动别名原子切换到已构建完成的集合。"""
    alias = active_alias_name()
    previous_target = await get_active_collection_target()
    operations: list = []
    if any(item.alias_name == alias for item in await _aliases()):
        operations.append(qmodels.DeleteAliasOperation(delete_alias=qmodels.DeleteAlias(alias_name=alias)))
    operations.append(
        qmodels.CreateAliasOperation(
            create_alias=qmodels.CreateAlias(collection_name=target_collection, alias_name=alias)
        )
    )
    await client.update_collection_aliases(change_aliases_operations=operations)
    logger.info(
        "rag.qdrant.alias.switched alias=%s previous=%s target=%s",
        alias,
        previous_target,
        target_collection,
    )
    return previous_target


async def upsert_chunks(chunks: list[dict], *, collection_name: str | None = None) -> None:
    """使用稳定标识和可检索的范围元数据，将分块持久化到 Qdrant。"""
    if not chunks:
        return
    validate_embedding_settings()
    if collection_name is None:
        await ensure_collection()
        collection_name = await get_active_collection_name()

    vectors = await embed_texts([str(chunk.get("text") or "") for chunk in chunks])
    points = [
        qmodels.PointStruct(
            id=_qdrant_point_id(chunk),
            vector=vector,
            payload=_prepare_payload(chunk),
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    await client.upsert(collection_name=collection_name, points=points, wait=True)
    logger.info("rag.upsert.done collection=%s point_count=%s", collection_name, len(points))


async def delete_document_chunks(document_id: int, *, collection_name: str | None = None) -> None:
    """从指定集合或当前活动集合中删除属于目标文档的全部分块。"""
    if collection_name is None:
        await ensure_collection()
        collection_name = await get_active_collection_name()
    await client.delete(
        collection_name=collection_name,
        points_selector=qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[qmodels.FieldCondition(key="document_id", match=qmodels.MatchValue(value=document_id))]
            )
        ),
        wait=True,
    )


async def replace_document_chunks(document_id: int, index_version: int, chunks: list[dict]) -> None:
    """发布文档的新版本后，仅移除该文档的旧版本分块。"""
    await ensure_collection()
    collection_name = await get_active_collection_name()
    await upsert_chunks(chunks, collection_name=collection_name)
    await client.delete(
        collection_name=collection_name,
        points_selector=qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[qmodels.FieldCondition(key="document_id", match=qmodels.MatchValue(value=document_id))],
                must_not=[
                    qmodels.FieldCondition(key="index_version", match=qmodels.MatchValue(value=index_version))
                ],
            )
        ),
        wait=True,
    )


def _legacy_compatible_condition(
    *,
    new_key: str,
    new_values: list[str] | list[int],
    legacy_key: str | None = None,
    legacy_values: list[str] | list[int] | None = None,
    missing_matches: bool = False,
) -> qmodels.Filter:
    """构造优先匹配新字段、兼容旧字段或缺失值的检索条件。"""
    options: list = [
        qmodels.FieldCondition(key=new_key, match=qmodels.MatchAny(any=new_values))
    ]
    if legacy_key and legacy_values:
        options.append(
            qmodels.Filter(
                must=[
                    qmodels.IsEmptyCondition(is_empty=qmodels.PayloadField(key=new_key)),
                    qmodels.FieldCondition(
                        key=legacy_key,
                        match=qmodels.MatchAny(any=legacy_values),
                    ),
                ]
            )
        )
    elif missing_matches:
        options.append(qmodels.IsEmptyCondition(is_empty=qmodels.PayloadField(key=new_key)))
    return qmodels.Filter(should=options)


def _build_filter(
    *,
    target_position: str | None,
    categories: list[str] | None,
    statuses: list[str] | None,
    versions: list[int] | None,
    include_signature: bool,
    include_general: bool,
) -> qmodels.Filter:
    """组合岗位、分类、状态、版本及向量签名条件，并兼容旧索引字段。"""
    must: list = []
    if include_signature:
        must.append(
            qmodels.FieldCondition(
                key="embedding_signature",
                match=qmodels.MatchValue(value=embedding_signature()),
            )
        )
    if target_position:
        normalized_positions = [normalize_scope_value(target_position)]
        legacy_positions = [target_position]
        if include_general:
            normalized_positions.append("general")
            legacy_positions.append("general")
        must.append(
            _legacy_compatible_condition(
                new_key="target_position_key",
                new_values=normalized_positions,
                legacy_key="target_position",
                legacy_values=legacy_positions,
            )
        )
    if categories:
        normalized_categories = sorted({normalize_scope_value(item) for item in categories if item})
        must.append(
            _legacy_compatible_condition(
                new_key="category_key",
                new_values=normalized_categories,
                legacy_key="category",
                legacy_values=categories,
            )
        )
    if statuses:
        must.append(
            _legacy_compatible_condition(
                new_key="document_status",
                new_values=statuses,
                missing_matches="ready" in statuses,
            )
        )
    if versions:
        must.append(
            _legacy_compatible_condition(
                new_key="index_version",
                new_values=versions,
                missing_matches=1 in versions,
            )
        )
    return qmodels.Filter(must=must)

async def search_chunks(
    query: str,
    limit: int = 5,
    *,
    target_position: str | None = None,
    categories: list[str] | None = None,
    statuses: list[str] | None = None,
    versions: list[int] | None = None,
    include_general: bool = True,
    legacy_title_scope: str | None = None,
) -> list[dict]:
    """按岗位与分类等范围调用高级检索，默认只召回已就绪的知识分块。"""
    return await search_chunks_advanced(
        query=query,
        limit=limit,
        target_position=target_position,
        categories=categories,
        statuses=statuses or ["ready"],
        versions=versions,
        include_general=include_general,
        legacy_title_scope=legacy_title_scope,
    )


async def search_chunks_advanced(
    query: str,
    limit: int = 5,
    *,
    target_position: str | None = None,
    categories: list[str] | None = None,
    statuses: list[str] | None = None,
    versions: list[int] | None = None,
    include_general: bool = True,
    legacy_title_scope: str | None = None,
) -> list[dict]:
    """在指定范围内执行向量与 BM25 召回、模型重排、相邻分块扩展和上下文预算控制。"""
    if not query:
        return []
    dense_limit = max(settings.rag_dense_candidate_limit, limit)
    bm25_limit = max(settings.rag_bm25_candidate_limit, limit)
    kwargs = {
        "target_position": target_position,
        "categories": categories,
        "statuses": statuses or ["ready"],
        "versions": versions,
        "include_general": include_general,
    }
    persistent_limit = MAX_PERSISTENT_CANDIDATES * 4 if legacy_title_scope else MAX_PERSISTENT_CANDIDATES
    vector_hits, persistent_candidates = await asyncio.gather(
        _vector_recall(query, dense_limit, **kwargs),
        _persistent_recall_candidates(limit=persistent_limit, **kwargs),
    )
    if legacy_title_scope:
        vector_hits = [item for item in vector_hits if _legacy_title_matches(item, legacy_title_scope)]
        persistent_candidates = [
            item for item in persistent_candidates if _legacy_title_matches(item, legacy_title_scope)
        ]
    bm25_hits = _bm25_recall(query, persistent_candidates, bm25_limit)
    fused = _rrf_fuse([vector_hits, bm25_hits])
    reranked = await _rerank_chunks(query, _deduplicate_chunks(fused), top_n=settings.rag_rerank_limit)
    selected = [_compress_chunk_for_context(query, chunk) for chunk in reranked[:limit]]
    expanded = await _expand_adjacent_chunks(selected)
    return _apply_context_token_budget(expanded, settings.rag_context_token_budget)


def _legacy_title_matches(chunk: dict, target_position: str) -> bool:
    """利用标题中的岗位信息限定旧版通用文档及按文件类型分类的文档范围。"""
    title_key = normalize_scope_value(str(chunk.get("title") or ""))
    target_key = normalize_scope_value(target_position)
    if not title_key or not target_key:
        return False
    if target_key in title_key:
        return True
    ascii_terms = [term.lower() for term in re.findall(r"[a-zA-Z0-9]+", target_position) if len(term) >= 2]
    return any(term in title_key for term in ascii_terms)


async def _vector_recall(
    query: str,
    limit: int,
    *,
    target_position: str | None = None,
    categories: list[str] | None = None,
    statuses: list[str] | None = None,
    versions: list[int] | None = None,
    include_general: bool = True,
) -> list[dict]:
    """生成查询向量并按范围召回相似分块，附上排名与分数，失败时返回空列表。"""
    try:
        await ensure_collection()
        collection_name, query_vector = await asyncio.gather(get_active_collection_name(), embed_text(query))
        response = await client.query_points(
            collection_name=collection_name,
            query=query_vector,
            query_filter=_build_filter(
                target_position=target_position,
                categories=categories,
                statuses=statuses or ["ready"],
                versions=versions,
                include_signature=True,
                include_general=include_general,
            ),
            score_threshold=settings.embedding_score_threshold,
            limit=limit,
        )
        results = []
        for rank, point in enumerate(response.points, start=1):
            payload = dict(point.payload or {})
            payload["_retrieval"] = {"route": "vector", "rank": rank, "score": float(point.score or 0)}
            results.append(payload)
        return results
    except Exception as exc:
        logger.warning("rag.search.vector.failed query=%r error=%r", query, exc)
        return []


async def _persistent_recall_candidates(
    *,
    limit: int,
    target_position: str | None = None,
    categories: list[str] | None = None,
    statuses: list[str] | None = None,
    versions: list[int] | None = None,
    include_general: bool = True,
) -> list[dict]:
    """从持久化集合读取限定范围内的分块载荷，供非向量召回使用，失败时返回空列表。"""
    try:
        await ensure_collection()
        collection_name = await get_active_collection_name()
        records, _ = await client.scroll(
            collection_name=collection_name,
            scroll_filter=_build_filter(
                target_position=target_position,
                categories=categories,
                statuses=statuses or ["ready"],
                versions=versions,
                include_signature=False,
                include_general=include_general,
            ),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [dict(record.payload or {}) for record in records]
    except Exception as exc:
        logger.warning("rag.search.persistent.failed error=%r", exc)
        return []


def _keyword_recall(query: str, candidates: list[dict], limit: int) -> list[dict]:
    """按查询词在分块检索文本中的匹配分数排序，返回带关键词召回信息的候选。"""
    terms = _query_terms(query)
    scored: list[tuple[float, dict]] = []
    for candidate in candidates:
        score = sum(_term_score(term, _chunk_search_text(candidate).lower()) for term in terms)
        if score > 0:
            scored.append((score, dict(candidate)))
    scored.sort(key=lambda item: (-item[0], _chunk_key(item[1])))
    results = []
    for rank, (score, payload) in enumerate(scored[:limit], start=1):
        payload["_retrieval"] = {"route": "keyword", "rank": rank, "score": score}
        results.append(payload)
    return results


def _bm25_recall(query: str, candidates: list[dict], limit: int) -> list[dict]:
    """对已持久化且经过范围过滤的候选分块计算 BM25 分数。"""
    terms = _query_terms(query)
    if not terms or not candidates:
        return []

    document_terms = [_query_terms(_chunk_search_text(candidate)) for candidate in candidates]
    lengths = [len(items) for items in document_terms]
    average_length = sum(lengths) / len(lengths) if lengths else 0.0
    if average_length <= 0:
        return []
    document_frequency = Counter(term for items in document_terms for term in set(items))
    query_frequency = Counter(terms)
    total_documents = len(candidates)
    k1 = 1.5
    b = 0.75
    scored: list[tuple[float, dict]] = []
    for candidate, items, document_length in zip(candidates, document_terms, lengths, strict=True):
        frequencies = Counter(items)
        score = 0.0
        for term, query_count in query_frequency.items():
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            inverse_document_frequency = math.log(
                1 + (total_documents - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)
            )
            denominator = frequency + k1 * (1 - b + b * document_length / average_length)
            score += query_count * inverse_document_frequency * frequency * (k1 + 1) / denominator
        if score > 0:
            scored.append((score, dict(candidate)))
    scored.sort(key=lambda item: (-item[0], _chunk_key(item[1])))
    results = []
    for rank, (score, payload) in enumerate(scored[:limit], start=1):
        payload["_retrieval"] = {"route": "bm25", "rank": rank, "score": score}
        results.append(payload)
    return results


def _metadata_recall(query: str, candidates: list[dict], limit: int) -> list[dict]:
    """按查询词在分块元数据中的匹配分数排序，并记录元数据召回信息。"""
    terms = _query_terms(query)
    scored: list[tuple[float, dict]] = []
    for candidate in candidates:
        score = sum(_term_score(term, _metadata_search_text(candidate).lower()) for term in terms)
        if score > 0:
            scored.append((score, dict(candidate)))
    scored.sort(key=lambda item: (-item[0], _chunk_key(item[1])))
    results = []
    for rank, (score, payload) in enumerate(scored[:limit], start=1):
        payload["_retrieval"] = {"route": "metadata", "rank": rank, "score": score}
        results.append(payload)
    return results

def _rrf_fuse(result_lists: list[list[dict]], k: int = RRF_K) -> list[dict]:
    """使用 RRF（倒数排名融合）合并多路召回结果。"""
    fused: dict[str, dict] = {}
    scores: dict[str, float] = {}
    routes: dict[str, set[str]] = {}
    route_scores: dict[str, dict[str, float]] = {}
    route_ranks: dict[str, dict[str, int]] = {}

    for result_list in result_lists:
        for rank, chunk in enumerate(result_list, start=1):
            key = _chunk_key(chunk)
            if key not in fused:
                fused[key] = dict(chunk)
                scores[key] = 0.0
                routes[key] = set()
                route_scores[key] = {}
                route_ranks[key] = {}
            scores[key] += 1.0 / (k + rank)
            retrieval = chunk.get("_retrieval") or {}
            route = str(retrieval.get("route") or "unknown")
            routes[key].add(route)
            route_scores[key][route] = float(retrieval.get("score") or 0.0)
            route_ranks[key][route] = int(retrieval.get("rank") or rank)

    for key, chunk in fused.items():
        chunk["_rrf_score"] = scores[key]
        chunk["_retrieval_routes"] = sorted(routes[key])
        chunk["_retrieval_scores"] = route_scores[key]
        chunk["_retrieval_ranks"] = route_ranks[key]

    return sorted(fused.values(), key=lambda item: (-float(item.get("_rrf_score") or 0.0), _chunk_key(item)))


def _deduplicate_chunks(chunks: list[dict]) -> list[dict]:
    """按 point id 和规范化文本去除重复 chunks。"""
    seen_keys: set[str] = set()
    seen_texts: set[str] = set()
    deduped: list[dict] = []

    for chunk in chunks:
        key = _chunk_key(chunk)
        normalized_text = _normalize_for_dedupe(str(chunk.get("text") or chunk.get("content") or ""))
        if key in seen_keys or normalized_text in seen_texts:
            continue
        seen_keys.add(key)
        if normalized_text:
            seen_texts.add(normalized_text)
        deduped.append(chunk)
    return deduped


async def _rerank_chunks(query: str, chunks: list[dict], *, top_n: int) -> list[dict]:
    """对融合后的候选 chunks 做轻量词法精排。"""
    if not chunks:
        return []
    try:
        ranked = await rerank_documents(query, [_chunk_search_text(chunk) for chunk in chunks], top_n=top_n)
    except RerankUnavailableError as exc:
        logger.warning("rag.rerank.fallback query=%r reason=%s", query, exc)
        return _lexical_rerank_chunks(query, chunks, fallback_reason=str(exc))[:top_n]

    ranked_chunks: list[dict] = []
    for rank, (index, score) in enumerate(ranked, start=1):
        chunk = dict(chunks[index])
        chunk["_rerank_score"] = score
        chunk["_rerank"] = {
            "provider": "dashscope",
            "model": settings.rerank_model,
            "rank": rank,
            "is_fallback": False,
        }
        ranked_chunks.append(chunk)
    return ranked_chunks[:top_n]


def _lexical_rerank_chunks(query: str, chunks: list[dict], *, fallback_reason: str) -> list[dict]:
    """结合词项、短语、召回路径和融合分数执行兜底重排，并记录降级原因。"""
    terms = _query_terms(query)
    query_lower = query.lower()
    reranked = []
    for chunk in chunks:
        text = _chunk_search_text(chunk).lower()
        lexical = sum(_term_score(term, text) for term in terms)
        phrase_bonus = 2.0 if query_lower and query_lower in text else 0.0
        route_bonus = len(chunk.get("_retrieval_routes") or []) * 0.2
        rrf_score = float(chunk.get("_rrf_score") or 0.0)
        score = lexical + phrase_bonus + route_bonus + rrf_score
        scored_chunk = dict(chunk)
        scored_chunk["_rerank_score"] = score
        scored_chunk["_rerank"] = {
            "provider": "lexical",
            "model": None,
            "is_fallback": True,
            "fallback_reason": fallback_reason,
        }
        reranked.append(scored_chunk)

    reranked.sort(
        key=lambda item: (
            -float(item.get("_rerank_score") or 0.0),
            int(item.get("document_id") or 0),
            int(item.get("chunk_index") or 0),
        )
    )
    return reranked


async def _expand_adjacent_chunks(chunks: list[dict]) -> list[dict]:
    """附加同一文档中紧邻命中分块的上下文，补充边界处的信息。"""
    window = settings.rag_adjacent_chunk_window
    if not chunks or window <= 0:
        return chunks
    document_ids = sorted({int(chunk.get("document_id") or 0) for chunk in chunks if chunk.get("document_id")})
    if not document_ids:
        return chunks
    try:
        await ensure_collection()
        collection_name = await get_active_collection_name()
        records, _ = await client.scroll(
            collection_name=collection_name,
            scroll_filter=qmodels.Filter(
                must=[qmodels.FieldCondition(key="document_id", match=qmodels.MatchAny(any=document_ids))]
            ),
            limit=MAX_PERSISTENT_CANDIDATES,
            with_payload=True,
            with_vectors=False,
        )
    except Exception as exc:
        logger.warning("rag.adjacent.failed document_ids=%s error=%r", document_ids, exc)
        return chunks

    by_document: dict[tuple[int, int], list[dict]] = {}
    for record in records:
        payload = dict(record.payload or {})
        key = (int(payload.get("document_id") or 0), int(payload.get("index_version") or 1))
        by_document.setdefault(key, []).append(payload)
    for items in by_document.values():
        items.sort(key=lambda item: int(item.get("chunk_index") or 0))

    expanded: list[dict] = []
    for chunk in chunks:
        key = (int(chunk.get("document_id") or 0), int(chunk.get("index_version") or 1))
        index = int(chunk.get("chunk_index") or 0)
        neighbors = [
            candidate for candidate in by_document.get(key, [])
            if candidate.get("document_status", "ready") == "ready"
            and abs(int(candidate.get("chunk_index") or 0) - index) <= window
        ]
        neighbors.sort(key=lambda item: int(item.get("chunk_index") or 0))
        if not neighbors:
            neighbors = [chunk]
        elif not any(int(candidate.get("chunk_index") or 0) == index for candidate in neighbors):
            neighbors.append(chunk)
            neighbors.sort(key=lambda item: int(item.get("chunk_index") or 0))

        enriched = dict(chunk)
        context_segments = []
        for neighbor in neighbors:
            is_primary = int(neighbor.get("chunk_index") or 0) == index
            source = chunk if is_primary else neighbor
            context_segments.append(_context_segment(source, is_primary=is_primary))
        enriched["_context_segments"] = context_segments
        enriched["text"] = "\n\n".join(segment["text"] for segment in context_segments).strip()
        enriched["_adjacent_chunk_ids"] = [
            _chunk_key(neighbor) for neighbor in neighbors
            if int(neighbor.get("chunk_index") or 0) != index
        ]
        expanded.append(enriched)
    return expanded


def _apply_context_token_budget(chunks: list[dict], budget: int) -> list[dict]:
    """为各智能体的知识上下文调用应用统一且可预期的词元预算。"""
    if budget <= 0:
        return []
    remaining = budget
    selected: list[dict] = []
    for chunk in chunks:
        if remaining <= 0:
            break

        raw_segments = chunk.get("_context_segments") or [_context_segment(chunk, is_primary=True)]
        indexed_segments = list(enumerate(raw_segments))
        indexed_segments.sort(key=lambda item: (not bool(item[1].get("is_primary")), item[0]))
        used: list[tuple[int, dict, str]] = []
        chunk_truncated = False
        for original_index, raw_segment in indexed_segments:
            segment = dict(raw_segment)
            text = str(segment.pop("text", ""))
            token_count = _estimate_tokens(text)
            if token_count <= remaining:
                segment["context_token_count"] = token_count
                used.append((original_index, segment, text))
                remaining -= token_count
                continue
            truncated = _truncate_to_token_budget(text, remaining)
            if truncated:
                used_token_count = _estimate_tokens(truncated)
                segment["context_token_count"] = used_token_count
                segment["context_truncated"] = True
                used.append((original_index, segment, truncated))
                remaining -= used_token_count
            chunk_truncated = True
            break

        if not used:
            break
        used.sort(key=lambda item: item[0])
        used_segments = [item[1] for item in used]
        used_texts = [item[2] for item in used]
        enriched = {key: value for key, value in chunk.items() if key != "_context_segments"}
        enriched["text"] = "\n\n".join(used_texts).strip()
        enriched["_context_chunks"] = used_segments
        enriched["_context_token_count"] = sum(int(item["context_token_count"]) for item in used_segments)
        if chunk_truncated:
            enriched["_context_truncated"] = True
        selected.append(enriched)
        if chunk_truncated:
            break
    return selected


def _context_segment(chunk: dict, *, is_primary: bool) -> dict:
    """将分块转换为包含标识、序号、页码、主片段标记和正文的上下文片段。"""
    metadata = chunk.get("metadata") or {}
    source_page = chunk.get("source_page") or metadata.get("source_page")
    return {
        "chunk_id": _chunk_key(chunk),
        "chunk_index": int(chunk.get("chunk_index") or 0),
        "source_page": int(source_page) if source_page not in (None, "") else None,
        "is_primary": is_primary,
        "text": str(chunk.get("text") or chunk.get("content") or ""),
    }


def _estimate_tokens(text: str) -> int:
    """按汉字逐字计数、其他文本每四个字符估算一个词元，估计上下文开销。"""
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    non_cjk = re.sub(r"[\u4e00-\u9fff]", "", text)
    non_cjk_tokens = max(1, math.ceil(len(non_cjk) / 4)) if non_cjk else 0
    return cjk + non_cjk_tokens


def _truncate_to_token_budget(text: str, budget: int) -> str:
    """逐字保留不超过估算词元预算的文本，并去掉结果首尾空白。"""
    if not text or budget <= 0:
        return ""
    result: list[str] = []
    for char in text:
        candidate = "".join(result) + char
        if _estimate_tokens(candidate) > budget:
            break
        result.append(char)
    return "".join(result).strip()


def _compress_chunk_for_context(query: str, chunk: dict, max_chars: int = MAX_CONTEXT_CHARS_PER_CHUNK) -> dict:
    """将过长 chunk 压缩为与 query 最相关的文本窗口。"""
    text = str(chunk.get("text") or chunk.get("content") or "")
    if len(text) <= max_chars:
        return chunk

    terms = _query_terms(query)
    windows = _split_text_windows(text, max_chars=max_chars)
    if not windows:
        return chunk

    best_window = max(windows, key=lambda window: sum(_term_score(term, window.lower()) for term in terms))
    compressed = dict(chunk)
    compressed["text"] = best_window.strip()
    compressed["_compressed"] = True
    compressed["_original_chars"] = len(text)
    return compressed


def _split_text_windows(text: str, max_chars: int) -> list[str]:
    """把长 chunk 按段落拆成多个可用于压缩的文本窗口。"""
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
    windows: list[str] = []
    current: list[str] = []
    current_len = 0

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                windows.append("\n\n".join(current))
                current = []
                current_len = 0
            for start in range(0, len(paragraph), max_chars):
                windows.append(paragraph[start : start + max_chars])
            continue
        if current and current_len + len(paragraph) > max_chars:
            windows.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(paragraph)
        current_len += len(paragraph)

    if current:
        windows.append("\n\n".join(current))
    return windows


def _query_terms(query: str) -> list[str]:
    """把 query 切成简单的英文、数字和中文词项。"""
    terms = [term.lower() for term in _WORD_RE.findall(query or "")]
    return [term for term in terms if term.strip()]


def _term_score(term: str, text: str) -> float:
    """计算某个 query 词项在文本中的命中强度。"""
    if not term:
        return 0.0
    count = text.count(term)
    if count <= 0:
        return 0.0
    return 1.0 + math.log(count)


def _chunk_key(chunk: dict) -> str:
    """返回 chunk 的稳定唯一标识，用于融合和去重。"""
    point_id = chunk.get("point_id")
    if point_id:
        return str(point_id)
    return f"{chunk.get('document_id', 0)}:{chunk.get('chunk_index', 0)}"


def _chunk_search_text(chunk: dict) -> str:
    """拼接 chunk 的正文和结构化信息，供精排打分使用。"""
    metadata = chunk.get("metadata") or {}
    heading_path = chunk.get("heading_path") or metadata.get("heading_path") or []
    if isinstance(heading_path, list):
        heading_text = " ".join(str(item) for item in heading_path)
    else:
        heading_text = str(heading_path)
    return " ".join(
        [
            str(chunk.get("title") or ""),
            str(chunk.get("category") or ""),
            str(chunk.get("target_position") or ""),
            str(chunk.get("section_title") or metadata.get("section_title") or ""),
            heading_text,
            str(chunk.get("text") or chunk.get("content") or ""),
        ]
    )


def _metadata_search_text(chunk: dict) -> str:
    """只拼接 chunk metadata 相关字段，供 metadata 召回使用。"""
    metadata = chunk.get("metadata") or {}
    return " ".join(
        [
            str(chunk.get("title") or ""),
            str(chunk.get("category") or ""),
            str(chunk.get("target_position") or ""),
            str(chunk.get("section_title") or metadata.get("section_title") or ""),
            str(chunk.get("heading_path") or metadata.get("heading_path") or ""),
            str(metadata.get("source_page") or ""),
        ]
    )


def _normalize_for_dedupe(text: str) -> str:
    """将 chunk 文本规范化为短签名，用于重复过滤。"""
    return re.sub(r"\s+", " ", text).strip().lower()[:500]
