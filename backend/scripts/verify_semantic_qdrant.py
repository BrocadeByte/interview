import asyncio

from sqlalchemy import select

from app.core.database import SessionLocal, engine
from app.models.knowledge import KnowledgeDocument
from app.rag.embeddings import embedding_signature

from app.rag.retriever import close_retriever_client, search_chunks_advanced


async def main() -> None:
    """逐文档执行语义检索，检查结果非空、向量配置一致且来自向量召回，最后关闭连接。"""
    async with SessionLocal() as db:
        documents = list(await db.scalars(select(KnowledgeDocument).order_by(KnowledgeDocument.id)))


    signature = embedding_signature()
    print({"documents": [{"id": doc.id, "title": doc.title} for doc in documents]})

    for document in documents:
        query = f"请检索与{document.title}语义相关的内容"
        results = await search_chunks_advanced(query, limit=3)
        if not results:
            raise RuntimeError(f"No semantic result for document: {document.title}")
        if any(item.get("embedding_signature") != signature for item in results):
            raise RuntimeError("Retrieved a vector from a different embedding space")
        if any("vector" not in (item.get("_retrieval_routes") or []) for item in results):
            raise RuntimeError("Fallback retrieval leaked into vector-only verification")
        print(
            {
                "query": query,
                "top_titles": [item.get("title") for item in results],
                "top_scores": [
                    round(float((item.get("_retrieval") or {}).get("score") or 0), 4)
                    for item in results
                ],
                "signature": signature,
            }
        )

    await close_retriever_client()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
