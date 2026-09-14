from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator


class RetrievalEvaluationCase(BaseModel):
    """一条检索评测样本：标准答案用于语义评测，相关文档用于确定性 ID 评测。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str | None = Field(default=None, max_length=80)
    query: str = Field(min_length=1, max_length=2000)
    reference: str | None = Field(default=None, max_length=12000)
    reference_document_ids: list[PositiveInt] = Field(default_factory=list, max_length=50)
    target_position: str | None = Field(default="general", max_length=160)
    categories: list[str] = Field(default_factory=list, max_length=20)
    include_general: bool = True

    @model_validator(mode="after")
    def validate_reference(self) -> "RetrievalEvaluationCase":
        self.query = self.query.strip()
        self.case_id = self.case_id.strip() if self.case_id else None
        self.reference = self.reference.strip() if self.reference else None
        self.target_position = self.target_position.strip() if self.target_position else None
        self.categories = list(dict.fromkeys(item.strip() for item in self.categories if item.strip()))
        self.reference_document_ids = list(dict.fromkeys(self.reference_document_ids))
        if not self.query:
            raise ValueError("检索问题不能为空")
        if not self.reference and not self.reference_document_ids:
            raise ValueError("每条样本至少需要标准答案或一个相关文档")
        return self


class RetrievalEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[RetrievalEvaluationCase] = Field(min_length=1, max_length=20)
    top_k: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def validate_case_ids(self) -> "RetrievalEvaluationRequest":
        case_ids = [item.case_id for item in self.cases if item.case_id]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("评测样本标识不能重复")
        return self


class RetrievalMetricScores(BaseModel):
    context_precision: float | None = Field(default=None, ge=0, le=1)
    context_recall: float | None = Field(default=None, ge=0, le=1)
    id_context_precision: float | None = Field(default=None, ge=0, le=1)
    id_context_recall: float | None = Field(default=None, ge=0, le=1)
    hit_rate: float | None = Field(default=None, ge=0, le=1)
    mrr: float | None = Field(default=None, ge=0, le=1)


class RetrievedContextRead(BaseModel):
    rank: int = Field(ge=1)
    document_id: int = Field(ge=1)
    title: str
    chunk_id: str
    chunk_index: int = Field(ge=0)
    content_preview: str
    retrieval_score: float
    retrieval_routes: list[str] = Field(default_factory=list)
    relevant: bool | None = None


class RetrievalEvaluationCaseResult(BaseModel):
    case_id: str
    query: str
    reference: str | None = None
    reference_document_ids: list[int] = Field(default_factory=list)
    metrics: RetrievalMetricScores
    retrieved_contexts: list[RetrievedContextRead] = Field(default_factory=list)
    metric_errors: dict[str, str] = Field(default_factory=dict)
    duration_ms: int = Field(ge=0)


class RetrievalEvaluationResult(BaseModel):
    framework: str
    framework_version: str
    evaluator_model: str | None = None
    top_k: int = Field(ge=1)
    case_count: int = Field(ge=1)
    metrics: RetrievalMetricScores
    cases: list[RetrievalEvaluationCaseResult]
    duration_ms: int = Field(ge=0)
