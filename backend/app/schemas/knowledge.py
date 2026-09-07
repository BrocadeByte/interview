from datetime import datetime

from pydantic import BaseModel, Field


class KnowledgeDocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    category: str = Field(min_length=1, max_length=120)
    target_position: str = Field(default="general", min_length=1, max_length=160)
    content: str = Field(min_length=1)
    metadata: dict = Field(default_factory=dict)


class KnowledgeDocumentUpdate(KnowledgeDocumentCreate):
    pass


class KnowledgeDocumentRead(BaseModel):
    id: int
    title: str
    category: str
    target_position: str
    content: str
    metadata: dict
    created_at: datetime
    updated_at: datetime


class KnowledgeReindexResult(BaseModel):
    status: str
    job_id: int
    document_count: int
    chunk_count: int
    processed_documents: int
    source_collection: str | None = None
    target_collection: str | None = None
    error: str | None = None
    recovery_strategy: str
