"""知识库 HTTP 接口：负责管理员权限和错误到 HTTP 状态的映射。"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.knowledge import (
    KnowledgeDocumentCreate,
    KnowledgeDocumentRead,
    KnowledgeDocumentUpdate,
    KnowledgeReindexResult,
)
from app.schemas.rag_evaluation import RetrievalEvaluationRequest, RetrievalEvaluationResult
from app.services.rag_evaluation_service import RagasUnavailableError, evaluate_retrieval
from app.services.knowledge_file_service import build_document_from_upload
from app.services.knowledge_service import (
    KnowledgeDataQualityError,
    KnowledgeIndexingError,
    KnowledgeReindexConflict,
    create_knowledge_document_and_commit,
    create_uploaded_knowledge_document_and_commit,
    delete_knowledge_document_and_commit,
    get_knowledge_document,
    get_reindex_job,
    list_knowledge_documents,
    reindex_knowledge_documents_and_commit,
    retry_knowledge_document_index_and_commit,
    update_knowledge_document_and_commit,
)


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/evaluations/retrieval", response_model=RetrievalEvaluationResult)
async def run_retrieval_evaluation(
    payload: RetrievalEvaluationRequest,
    current_user: User = Depends(get_current_admin_user),
) -> RetrievalEvaluationResult:
    """使用 Ragas 运行管理员提交的检索评测集，返回汇总与逐条明细。"""
    del current_user
    try:
        return await evaluate_retrieval(payload)
    except RagasUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


# 创建一篇知识库文档。
@router.post("/documents", response_model=KnowledgeDocumentRead, status_code=status.HTTP_201_CREATED)
async def create_document(
    payload: KnowledgeDocumentCreate,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeDocumentRead:
    """创建知识文档，并把领域错误转换为稳定的 HTTP 响应。"""
    del current_user
    try:
        document = await create_knowledge_document_and_commit(db, payload)
    except KnowledgeDataQualityError as exc:
        raise _quality_http_error(exc) from exc
    except KnowledgeIndexingError as exc:
        raise _indexing_http_error(exc) from exc
    return document


# 获取全部知识库文档。
@router.get("/documents", response_model=list[KnowledgeDocumentRead])
async def list_documents(
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[KnowledgeDocumentRead]:
    """返回全部知识库文档。"""
    del current_user
    return await list_knowledge_documents(db)


# 获取指定知识库文档。
@router.get("/documents/{document_id}", response_model=KnowledgeDocumentRead)
async def get_document(
    document_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeDocumentRead:
    """返回指定知识库文档。"""
    del current_user
    document = await get_knowledge_document(db, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


# 更新指定知识库文档。
@router.put("/documents/{document_id}", response_model=KnowledgeDocumentRead)
async def update_document(
    document_id: int,
    payload: KnowledgeDocumentUpdate,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeDocumentRead:
    """更新知识文档，并映射质量检查或索引失败。"""
    del current_user
    try:
        document = await update_knowledge_document_and_commit(db, document_id, payload)
    except KnowledgeDataQualityError as exc:
        raise _quality_http_error(exc) from exc
    except KnowledgeIndexingError as exc:
        raise _indexing_http_error(exc) from exc
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


# 删除指定知识库文档。
@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """删除知识文档，并映射向量索引删除失败。"""
    del current_user
    try:
        deleted = await delete_knowledge_document_and_commit(db, document_id)
    except KnowledgeIndexingError as exc:
        raise _indexing_http_error(exc) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return None


# 解析上传文件并创建知识库文档。
@router.post("/files", response_model=KnowledgeDocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_document_file(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    category: str = Form(...),
    target_position: str | None = Form(default=None),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeDocumentRead:
    """解析上传文件并创建知识库文档。"""
    del current_user
    parsed_upload = await build_document_from_upload(file, title, category, target_position)
    try:
        document = await create_uploaded_knowledge_document_and_commit(db, parsed_upload)
    except KnowledgeDataQualityError as exc:
        raise _quality_http_error(exc) from exc
    except KnowledgeIndexingError as exc:
        raise _indexing_http_error(exc) from exc
    return document



@router.post("/documents/{document_id}/retry-index", response_model=KnowledgeDocumentRead)
async def retry_document_index(
    document_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeDocumentRead:
    """重试指定文档的向量索引。"""
    del current_user
    try:
        document = await retry_knowledge_document_index_and_commit(db, document_id)
    except KnowledgeDataQualityError as exc:
        raise _quality_http_error(exc) from exc
    except KnowledgeIndexingError as exc:
        raise _indexing_http_error(exc) from exc
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document

# 重建知识库文档索引。
@router.post("/reindex", response_model=KnowledgeReindexResult)
async def reindex_knowledge(
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeReindexResult:
    """重建全部知识文档的向量索引。"""
    del current_user
    try:
        result = await reindex_knowledge_documents_and_commit(db)
    except KnowledgeReindexConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return result


@router.get("/reindex/{job_id}", response_model=KnowledgeReindexResult)
async def get_knowledge_reindex_job(
    job_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeReindexResult:
    """返回指定全量重建任务的状态。"""
    del current_user
    result = await get_reindex_job(db, job_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reindex job not found")
    return result


def _indexing_http_error(exc: KnowledgeIndexingError) -> HTTPException:
    """将文档已保存但索引失败的异常转换为可重试的服务不可用响应。"""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"Document {exc.document_id} was saved but indexing failed; retry indexing later",
    )

def _quality_http_error(exc: KnowledgeDataQualityError) -> HTTPException:
    """将知识文档质量检查失败的原因转换为无法处理实体的接口响应。"""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"Document {exc.document_id} failed quality checks: {exc.reason}",
    )
