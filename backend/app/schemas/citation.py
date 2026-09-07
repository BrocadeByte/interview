from pydantic import BaseModel, ConfigDict, Field


class KnowledgeContextChunkCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    chunk_index: int
    source_page: int | None = None
    is_primary: bool
    context_token_count: int = Field(default=0, ge=0)
    context_truncated: bool = False


class KnowledgeCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: int = Field(ge=1)
    query: str
    purpose: str | None = None
    question_index: int | None = Field(default=None, ge=1)
    document_id: int = Field(ge=1)
    title: str
    category: str
    target_position: str
    index_version: int = Field(ge=1)
    chunk_id: str
    chunk_index: int = Field(ge=0)
    source_page: int | None = Field(default=None, ge=1)
    section_title: str | None = None
    retrieval_score: float
    retrieval_routes: list[str] = Field(default_factory=list)
    retrieval_scores: dict[str, float] = Field(default_factory=dict)
    retrieval_ranks: dict[str, int] = Field(default_factory=dict)
    rrf_score: float = 0.0
    rerank_score: float = 0.0
    rerank_provider: str | None = None
    rerank_model: str | None = None
    rerank_rank: int | None = Field(default=None, ge=1)
    rerank_is_fallback: bool = False
    rerank_fallback_reason: str | None = None
    context_chunks: list[KnowledgeContextChunkCitation] = Field(default_factory=list)
    context_token_count: int = Field(default=0, ge=0)
    context_truncated: bool = False


class KnowledgeContextResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    citations: list[KnowledgeCitation] = Field(default_factory=list)
