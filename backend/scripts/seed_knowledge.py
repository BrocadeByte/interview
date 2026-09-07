import asyncio
import sys
from pathlib import Path

from sqlalchemy import select


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import app.models  # noqa: F401  导入模型以注册表元数据，保留未使用导入的检查豁免。
from app.core.database import Base, SessionLocal, engine
from app.models.knowledge import KnowledgeDocument
from app.schemas.knowledge import KnowledgeDocumentCreate
from app.services.knowledge_service import create_knowledge_document, format_knowledge_context


SEED_SOURCE = "python_backend_seed"
SEED_VERSION = 1


SEED_DOCUMENTS = [
    KnowledgeDocumentCreate(
        title="Python 后端岗位能力模型",
        category="岗位能力模型",
        target_position="Python 后端工程师",
        content="""
一、核心能力
- Python 基础：数据结构、迭代器、生成器、异常处理、上下文管理器、类型标注。
- Web 框架：理解 FastAPI / Flask 的路由、依赖注入、中间件、异常处理、请求响应模型。
- 数据库：掌握 MySQL 表设计、索引、事务隔离级别、SQL 优化和慢查询排查。
- 缓存与中间件：理解 Redis 缓存、分布式锁、限流、会话管理、消息队列基础。
- 工程实践：日志、配置管理、单元测试、接口文档、代码分层、错误处理、部署排查。
- 安全意识：密码哈希、JWT、权限校验、接口防刷、敏感信息保护。

二、考察重点
- 能否结合真实项目讲清楚背景、职责、方案、取舍、结果。
- 能否说明为什么这样设计，而不是只罗列技术名词。
- 能否在追问下讲清楚关键实现细节和边界情况。
- 能否对性能、安全、可维护性有基本判断。
""".strip(),
        metadata={"source": SEED_SOURCE, "version": SEED_VERSION},
    ),
    KnowledgeDocumentCreate(
        title="FastAPI 接口设计评分标准",
        category="评分标准",
        target_position="Python 后端工程师",
        content="""
一、优秀回答特征
- 能说明接口分层：路由层、Schema 校验、Service 业务层、Model 持久层。
- 能说明请求参数校验、响应模型、异常处理和统一错误返回。
- 能说明依赖注入在鉴权、数据库会话、配置读取中的使用。
- 能说明异步接口的收益和限制，例如 IO 密集场景适合 async，CPU 密集场景不适合盲目 async。
- 能说明接口文档、版本管理、幂等性、分页、过滤、排序等工程实践。

二、常见扣分点
- 只说用了 FastAPI，不知道 Pydantic、Depends、中间件等机制。
- 不区分业务异常和系统异常。
- 不知道数据库会话生命周期如何管理。
- 没有考虑接口鉴权、参数校验、日志和错误处理。

三、追问方向
- 你的接口如何做统一异常处理？
- 数据库 Session 是怎么注入和释放的？
- 如果接口很慢，你会如何定位？
- 如何设计分页查询和条件筛选？
""".strip(),
        metadata={"source": SEED_SOURCE, "version": SEED_VERSION},
    ),
    KnowledgeDocumentCreate(
        title="JWT 登录鉴权评分标准",
        category="评分标准",
        target_position="Python 后端工程师",
        content="""
一、必须覆盖的点
- 密码不能明文存储，应使用 bcrypt、argon2 等慢哈希算法。
- 登录成功后签发 JWT，token 中只放必要身份信息和过期时间。
- 后续接口通过 Authorization: Bearer Token 传递令牌。
- 服务端需要校验签名、过期时间、用户是否存在、权限是否满足。

二、加分点
- 能说明 access token 和 refresh token 的区别。
- 能说明 token 过期、刷新、登出失效、黑名单或版本号机制。
- 能提到密钥管理、HTTPS、HttpOnly Cookie、CSRF、防暴力破解。
- 能解释 JWT 与 Session 的取舍。

三、常见扣分点
- 只说“用了 JWT”，说不清签发和校验流程。
- 不知道 token 过期如何处理。
- 把敏感信息放进 token payload。
- 不知道密码哈希和加密的区别。

四、追问方向
- 用户登出后如何让 JWT 失效？
- refresh token 泄露怎么办？
- 多端登录如何管理？
- 密钥轮换如何处理？
""".strip(),
        metadata={"source": SEED_SOURCE, "version": SEED_VERSION},
    ),
    KnowledgeDocumentCreate(
        title="MySQL 索引事务评分标准",
        category="评分标准",
        target_position="Python 后端工程师",
        content="""
一、索引能力
- 能说明 B+Tree 索引、联合索引、最左前缀、覆盖索引、回表。
- 能说明哪些场景适合建索引，哪些场景索引可能失效。
- 能使用 EXPLAIN 分析查询计划。
- 能结合慢查询日志定位 SQL 性能问题。

二、事务能力
- 能说明 ACID、事务隔离级别、脏读、不可重复读、幻读。
- 能说明 InnoDB 的 MVCC、行锁、间隙锁的基本概念。
- 能结合业务场景说明为什么需要事务。

三、常见追问
- 联合索引 `(a,b,c)` 在哪些查询条件下能命中？
- 为什么 `like '%keyword'` 通常不能有效使用索引？
- 如何处理库存扣减并发问题？
- 慢 SQL 你会怎么排查？
""".strip(),
        metadata={"source": SEED_SOURCE, "version": SEED_VERSION},
    ),
    KnowledgeDocumentCreate(
        title="Redis 缓存限流评分标准",
        category="评分标准",
        target_position="Python 后端工程师",
        content="""
一、缓存能力
- 能说明缓存命中、缓存穿透、缓存击穿、缓存雪崩。
- 能说明布隆过滤器、互斥锁、随机过期时间、热点 key 保护等解决方案。
- 能说明缓存与数据库一致性的常见策略，例如 Cache Aside。

二、限流能力
- 能说明固定窗口、滑动窗口、令牌桶、漏桶等限流算法。
- 能结合 Redis 实现接口限流、防刷、验证码频控。

三、分布式能力
- 能说明 Redis 分布式锁基本实现和过期时间问题。
- 能说明 set nx ex、Lua 脚本原子性、锁续期、误删锁风险。

四、追问方向
- 缓存击穿和缓存穿透有什么区别？
- 如何保证缓存和数据库最终一致？
- Redis 限流如何设计 key？
- 分布式锁为什么需要唯一 value？
""".strip(),
        metadata={"source": SEED_SOURCE, "version": SEED_VERSION},
    ),
    KnowledgeDocumentCreate(
        title="Python 后端项目经验追问题库",
        category="追问策略",
        target_position="Python 后端工程师",
        content="""
一、项目背景追问
- 这个项目解决了什么真实问题？目标用户是谁？
- 你负责哪一部分？是独立完成还是团队协作？
- 项目上线了吗？有多少用户、数据量或请求量？

二、技术方案追问
- 为什么选择 FastAPI / MySQL / Redis / JWT？有没有比较过其他方案？
- 核心表结构是怎么设计的？为什么这样设计？
- 接口鉴权、异常处理、日志记录是怎么做的？

三、难点和结果追问
- 项目中最大的技术难点是什么？你怎么定位和解决？
- 有没有出现过线上问题？如何回滚、修复和复盘？
- 做过哪些性能优化？优化前后有什么指标变化？

四、复盘追问
- 如果重新做一遍，你会怎么优化架构？
- 哪些地方现在看还有技术债？
""".strip(),
        metadata={"source": SEED_SOURCE, "version": SEED_VERSION},
    ),
    KnowledgeDocumentCreate(
        title="线上问题排查追问题库",
        category="追问策略",
        target_position="Python 后端工程师",
        content="""
一、接口变慢
- 你会先看哪些指标？响应时间、错误率、QPS、CPU、内存、数据库连接数？
- 如何区分是应用慢、数据库慢、网络慢还是第三方服务慢？
- 慢 SQL 如何定位？如何验证优化是否有效？

二、接口报错
- 如何通过日志定位请求链路？是否有 request_id 或 trace_id？
- 线上异常如何止血？重启、回滚、降级、限流分别适合什么情况？
- 修复后如何复盘，避免再次发生？

三、数据异常
- 如果用户反馈数据不一致，你如何排查？
- 如何确认是缓存问题、事务问题还是业务逻辑问题？
- 修复数据时如何保证可追溯和可回滚？

四、评分关注
- 是否有清晰排查步骤。
- 是否能结合日志、监控、数据库和代码定位。
- 是否考虑止血、修复、复盘三个阶段。
""".strip(),
        metadata={"source": SEED_SOURCE, "version": SEED_VERSION},
    ),
    KnowledgeDocumentCreate(
        title="Python 后端优秀回答样例",
        category="优秀回答样例",
        target_position="Python 后端工程师",
        content="""
一、项目经验回答结构
1. 背景：说明业务场景、用户、目标和约束。
2. 职责：说明自己负责的模块和边界。
3. 方案：说明技术选型、核心设计和关键取舍。
4. 实现：说明接口、表结构、鉴权、缓存、异常处理等细节。
5. 结果：说明性能、稳定性、用户量、效率提升等指标。
6. 复盘：说明不足和后续优化方向。

二、JWT 鉴权示范回答要点
- 注册时使用 bcrypt 哈希密码，不保存明文。
- 登录时校验密码，签发带用户 ID 和过期时间的 access token。
- 接口通过依赖注入解析 Bearer Token，校验签名和过期时间。
- 高安全场景会引入 refresh token、黑名单或 token version 支持登出失效。
- 会限制登录失败次数，避免暴力破解。

三、线上问题示范回答要点
- 先通过监控确认影响范围和指标变化。
- 根据 request_id 查看日志链路。
- 排查数据库慢查询、缓存命中率、外部服务耗时。
- 先止血，再修复，最后复盘并补监控和测试。
""".strip(),
        metadata={"source": SEED_SOURCE, "version": SEED_VERSION},
    ),
]


async def seed_documents() -> None:
    """按标题跳过已存在的种子知识，创建其余文档与索引，提交后执行一次检索检查。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:
        existing_titles = set(await db.scalars(select(KnowledgeDocument.title)))
        created_titles: list[str] = []
        skipped_titles: list[str] = []

        for document in SEED_DOCUMENTS:
            if document.title in existing_titles:
                skipped_titles.append(document.title)
                continue
            created = await create_knowledge_document(db, document)
            created_titles.append(created.title)

        await db.commit()

    print(f"created: {len(created_titles)}")
    for title in created_titles:
        print(f"+ {title}")
    print(f"skipped: {len(skipped_titles)}")
    for title in skipped_titles:
        print(f"= {title}")

    print("\nquery check:")
    print(await format_knowledge_context("Python 后端 JWT 登录鉴权 token 过期 刷新", limit=3))


if __name__ == "__main__":
    asyncio.run(seed_documents())
