import asyncio
import hashlib
import logging
import math
import time
from collections import OrderedDict

import aiohttp

from app.core.config import settings


logger = logging.getLogger(__name__)
DASHSCOPE_BATCH_SIZE = 10


# 进程级共享 aiohttp 会话：避免每次向量检索都新建 TCP+TLS 连接到 DashScope。
_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()

# 单条查询向量的 TTL 缓存：面试计划、报告等重复 query 直接命中，省一次 embedding HTTP。
_embedding_cache: "OrderedDict[str, tuple[float, list[float]]]" = OrderedDict()
_embedding_inflight: dict[str, asyncio.Task[list[float]]] = {}
_embedding_inflight_lock = asyncio.Lock()


def embedding_signature() -> str:
    """组合向量服务提供方、模型和维度，生成区分缓存与索引版本的配置签名。"""
    return f"{settings.embedding_provider.strip().lower()}:{settings.embedding_model}:{settings.embedding_dim}"


def validate_embedding_settings() -> None:
    """检查向量服务类型、所需密钥及测试哈希向量的启用条件，不满足时抛出异常。"""
    provider = settings.embedding_provider.strip().lower()
    if provider == "dashscope" and not settings.dashscope_api_key:
        raise ValueError("DASHSCOPE_API_KEY is required when EMBEDDING_PROVIDER=dashscope")
    if provider == "hash" and not settings.allow_hash_embeddings:
        raise ValueError("Hash embeddings are test-only; set ALLOW_HASH_EMBEDDINGS=true explicitly")
    if provider not in {"dashscope", "hash"}:
        raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")


async def _get_session() -> aiohttp.ClientSession:
    """返回进程级共享 aiohttp 会话，已关闭时按需重建。"""
    global _session
    if _session is not None and not _session.closed:
        return _session
    async with _session_lock:
        if _session is None or _session.closed:
            timeout = aiohttp.ClientTimeout(total=settings.embedding_timeout_seconds)
            _session = aiohttp.ClientSession(timeout=timeout)
    return _session


async def close_embedding_session() -> None:
    """进程退出时关闭共享会话，避免资源泄漏告警。"""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


def _cache_get(key: str) -> list[float] | None:
    """读取有效期内的向量缓存并更新最近使用顺序，过期项会被移除。"""
    now = time.monotonic()
    ttl = settings.embedding_cache_ttl_seconds
    entry = _embedding_cache.get(key)
    if entry and now - entry[0] < ttl:
        _embedding_cache.move_to_end(key)
        return entry[1]
    if entry:
        _embedding_cache.pop(key, None)
    return None


def _cache_put(key: str, vector: list[float]) -> None:
    """缓存向量及写入时间，超出容量时优先淘汰最久未使用的条目。"""
    _embedding_cache[key] = (time.monotonic(), vector)
    _embedding_cache.move_to_end(key)
    while len(_embedding_cache) > settings.embedding_cache_size:
        _embedding_cache.popitem(last=False)


async def embed_text(text: str) -> list[float]:
    """缓存查询向量，并合并同一文本的并发向量请求。"""
    cache_key = f"{embedding_signature()}|{text}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    async with _embedding_inflight_lock:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        task = _embedding_inflight.get(cache_key)
        if task is None:
            task = asyncio.create_task(_embed_and_cache(cache_key, text))
            _embedding_inflight[cache_key] = task
            task.add_done_callback(
                lambda completed, key=cache_key: _embedding_inflight.pop(key, None)
                if _embedding_inflight.get(key) is completed
                else None
            )

    return await asyncio.shield(task)


async def _embed_and_cache(cache_key: str, text: str) -> list[float]:
    """生成单条文本的向量，写入查询缓存后返回结果。"""
    vector = (await embed_texts([text]))[0]
    _cache_put(cache_key, vector)
    return vector


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """以异步方式批量生成文本向量，避免阻塞事件循环。"""
    if not texts:
        return []
    validate_embedding_settings()
    provider = settings.embedding_provider.strip().lower()
    if provider == "hash":
        return [hash_embed_text(text, dim=settings.embedding_dim) for text in texts]

    session = await _get_session()
    vectors: list[list[float]] = []
    for start in range(0, len(texts), DASHSCOPE_BATCH_SIZE):
        vectors.extend(
            await dashscope_embed_texts(
                texts[start : start + DASHSCOPE_BATCH_SIZE],
                session=session,
            )
        )
    return vectors


def hash_embed_text(text: str, dim: int = 64) -> list[float]:
    """按文本词元的哈希值累加并归一化向量，供明确启用的测试场景使用。"""
    vector = [0.0] * dim
    if not text:
        return vector
    tokens = text.lower().split() or list(text)
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = digest[0] % dim
        value = (digest[1] / 255.0) * 2 - 1
        vector[index] += value
    return normalize_vector(vector)


async def dashscope_embed_texts(
    texts: list[str],
    *,
    session: aiohttp.ClientSession,
) -> list[list[float]]:
    """调用 DashScope 批量向量接口，按输入顺序整理结果并校验数量与维度。"""
    payload = {
        "model": settings.embedding_model,
        "input": {"texts": [text or " " for text in texts]},
        "parameters": {"dimension": settings.embedding_dim},
    }
    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with session.post(settings.dashscope_embedding_url, json=payload, headers=headers) as response:
            body = await response.text()
            if response.status >= 400:
                logger.error("embedding.dashscope.http_error status=%s body=%s", response.status, body[:500])
                response.raise_for_status()
            data = await response.json()
    except Exception as exc:
        logger.error("embedding.dashscope.failed error=%r", exc)
        raise

    items = data.get("output", {}).get("embeddings") or []
    if len(items) != len(texts):
        raise ValueError(f"DashScope returned {len(items)} embeddings for {len(texts)} texts")
    ordered = sorted(items, key=lambda item: int(item.get("text_index", 0)))
    vectors = [[float(value) for value in item.get("embedding") or []] for item in ordered]
    for vector in vectors:
        if len(vector) != settings.embedding_dim:
            raise ValueError(f"Embedding dimension mismatch: expected {settings.embedding_dim}, got {len(vector)}")
    return vectors


async def dashscope_embed_text(text: str) -> list[float]:
    """创建带超时的网络会话，通过 DashScope 生成单条文本向量。"""
    timeout = aiohttp.ClientTimeout(total=settings.embedding_timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        return (await dashscope_embed_texts([text], session=session))[0]


def normalize_vector(vector: list[float]) -> list[float]:
    """按欧几里得范数将向量归一化，零向量保持原样。"""
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]