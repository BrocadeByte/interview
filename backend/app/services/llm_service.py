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


def bind_json_output(model):
    """为需要 JSON 对象的单次调用绑定 DeepSeek/OpenAI 兼容参数。

    运行时绑定使测试替身和不同节点仍能共用全局模型，同时不会向
    全局模型添加 thinking 配置。
    """
    bind = getattr(model, "bind", None)
    if not callable(bind):
        return model
    return bind(response_format={"type": "json_object"})
