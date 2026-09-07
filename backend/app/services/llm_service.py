from langchain_openai import ChatOpenAI

from app.core.config import settings


# 这个文件只做一件事：创建一个全项目共用的 LLM 对象。
# 其他节点需要调用模型时，直接 from app.services.llm_service import llm。
llm = ChatOpenAI(
    model=settings.openai_model,
    temperature=0.7,
    api_key=settings.openai_api_key,
    base_url=settings.openai_api_base,
    timeout=settings.llm_request_timeout_seconds,
    max_retries=settings.llm_max_retries,
)
