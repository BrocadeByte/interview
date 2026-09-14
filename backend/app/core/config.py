from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Interview System"
    database_url: str = "mysql+aiomysql://root:123456@127.0.0.1:3306/ai_interview?charset=utf8mb4"
    jwt_secret_key: str = "change-this-secret-in-production"
    refresh_jwt_secret_key: str = "change-this-refresh-secret-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30
    refresh_token_reuse_grace_seconds: int = 10
    refresh_cookie_name: str = "refresh_token"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    auth_cookie_domain: str | None = None
    auth_cookie_path: str = "/api/auth"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    openai_api_key: str = ""
    openai_api_base: str = "https://api.xiaomimimo.com/v1"
    openai_model: str = "deepseek-v4-flash"
    qdrant_url: str = "http://192.168.150.101:6333"
    qdrant_collection_name: str = "knowledge_documents"
    embedding_provider: str = "dashscope"
    embedding_model: str = "text-embedding-v4"
    embedding_dim: int = 1024
    embedding_score_threshold: float = 0.35
    embedding_timeout_seconds: int = 30
    allow_hash_embeddings: bool = False
    dashscope_api_key: str = ""
    dashscope_embedding_url: str = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"
    rerank_enabled: bool = True
    rerank_model: str = "gte-rerank-v2"
    rerank_timeout_seconds: int = 20
    dashscope_rerank_url: str = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    rag_dense_candidate_limit: int = 30
    rag_bm25_candidate_limit: int = 30
    rag_rerank_limit: int = 8
    rag_adjacent_chunk_window: int = 1
    rag_context_token_budget: int = 6000
    ragas_evaluator_model: str = ""
    ragas_evaluation_timeout_seconds: int = 120
    ragas_max_concurrency: int = 2
    aliyun_oss_region: str = ""
    aliyun_oss_bucket: str = ""
    aliyun_oss_endpoint: str = ""
    aliyun_oss_prefix: str = "knowledge/"
    db_pool_size: int = 20
    db_max_overflow: int = 20
    db_pool_recycle_seconds: int = 1800
    db_pool_timeout_seconds: int = 30
    llm_request_timeout_seconds: int = 120
    llm_max_retries: int = 2
    resume_parse_model: str = ""
    resume_parse_timeout_seconds: int = 120
    resume_parse_json_mode: bool = True
    resume_parse_thinking_mode: Literal["default", "enabled", "disabled"] = "disabled"
    embedding_cache_size: int = 512
    embedding_cache_ttl_seconds: int = 3600
    rabbitmq_url: str = "amqp://admin:admin123@192.168.150.101:5672/"
    rabbitmq_ingestion_queue: str = "knowledge.ingestion"
    rabbitmq_ingestion_exchange: str = "knowledge.ingestion.exchange"
    rabbitmq_ingestion_dlx: str = "knowledge.ingestion.dlx"
    rabbitmq_resume_queue: str = "resume.parsing"
    rabbitmq_resume_exchange: str = "resume.parsing.exchange"
    rabbitmq_resume_dlx: str = "resume.parsing.dlx"
    rabbitmq_prefetch_count: int = 2
    knowledge_ingestion_max_attempts: int = 3
    rabbitmq_connect_timeout_seconds: int = 10
    healthcheck_timeout_seconds: float = 2.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
