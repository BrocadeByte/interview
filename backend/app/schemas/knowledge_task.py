from datetime import datetime

from pydantic import BaseModel


class KnowledgeIngestionTaskRead(BaseModel):
    id: int
    document_id: int | None = None
    status: str
    stage: str
    title: str
    category: str
    target_position: str
    file_name: str
    file_type: str
    file_size: int
    attempts: int
    max_attempts: int
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
