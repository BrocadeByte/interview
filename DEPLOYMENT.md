# Linux Docker 部署与运维

本文用于在单台 Linux 服务器上部署和维护 AI 模拟面试官。部署由项目根目录的 `compose.yaml` 管理，不依赖手动创建的 Docker 网络或外部 Qdrant 容器。

## 1. 环境要求

- 64 位 Linux 服务器；
- Docker Engine；
- Docker Compose 插件；
- 可访问所选大模型与 DashScope API 的网络；
- 建议至少 4 核 CPU、8 GB 内存，并预留数据库和向量索引磁盘空间。

确认环境：

```bash
docker --version
docker compose version
```

## 2. 获取项目并准备配置

```bash
git clone https://github.com/BrocadeByte/interview.git
cd interview
cp .env.production.example .env.production
```

生成两段独立的 JWT 密钥：

```bash
openssl rand -hex 32
openssl rand -hex 32
```

编辑 `.env.production`，至少替换以下项目：

```env
MYSQL_PASSWORD=数据库用户密码
MYSQL_ROOT_PASSWORD=数据库_root_密码
RABBITMQ_PASSWORD=消息队列密码
JWT_SECRET_KEY=第一段随机密钥
REFRESH_JWT_SECRET_KEY=第二段独立随机密钥
OPENAI_API_KEY=大模型_API_Key
DASHSCOPE_API_KEY=DashScope_API_Key
```

密码会进入连接 URL，建议只使用字母、数字、下划线、短横线或点等 URL 安全字符。不要把 `.env.production` 提交到 Git。

## 3. 首次启动

先验证 Compose 配置：

```bash
docker compose --env-file .env.production config --quiet
docker compose --env-file .env.production config --services
```

当前编排包含：

| 服务 | 作用 |
| --- | --- |
| `frontend` | 构建 Vue 应用并通过 Nginx 提供统一入口 |
| `backend` | FastAPI 接口服务 |
| `resume-worker` | 异步解析简历 |
| `knowledge-worker` | 异步处理和索引知识文档 |
| `mysql` | 业务数据库 |
| `rabbitmq` | 简历与知识库任务队列 |
| `qdrant` | 向量数据库 |

构建并启动：

```bash
docker compose --env-file .env.production up -d --build
docker compose --env-file .env.production ps
```

MySQL 与 RabbitMQ 首次初始化可能需要几十秒；后端会在依赖服务健康后启动。

## 4. 访问与健康检查

默认访问地址：

```text
应用首页          http://服务器地址/
API 文档          http://服务器地址/docs
前端健康检查      http://服务器地址/health
后端存活检查      http://服务器地址/api/health/live
后端就绪检查      http://服务器地址/api/health/ready
RabbitMQ 管理页   http://127.0.0.1:15672
```

在服务器上检查：

```bash
curl -fsS http://127.0.0.1/health
curl -fsS http://127.0.0.1/api/health/live
curl -fsS http://127.0.0.1/api/health/ready
```

如果 80 端口已占用，可以修改：

```env
HTTP_PORT=8080
```

## 5. 日常操作

查看状态：

```bash
docker compose --env-file .env.production ps
```

启动已有容器：

```bash
docker compose --env-file .env.production start
```

停止但保留容器和数据：

```bash
docker compose --env-file .env.production stop
```

停止并删除应用容器，但保留命名卷：

```bash
docker compose --env-file .env.production down
```

不要在没有完整备份时执行以下命令：

```bash
docker compose --env-file .env.production down -v
```

`-v` 会删除 MySQL、RabbitMQ 和 Qdrant 的命名卷数据。

## 6. 查看日志

查看全部服务：

```bash
docker compose --env-file .env.production logs -f --tail=100
```

按服务查看：

```bash
docker compose --env-file .env.production logs -f --tail=200 backend
docker compose --env-file .env.production logs -f --tail=200 resume-worker
docker compose --env-file .env.production logs -f --tail=200 knowledge-worker
docker compose --env-file .env.production logs -f --tail=200 frontend
docker compose --env-file .env.production logs -f --tail=200 mysql
docker compose --env-file .env.production logs -f --tail=200 rabbitmq
docker compose --env-file .env.production logs -f --tail=200 qdrant
```

Compose 已启用日志轮转：单个日志文件最多 10 MB，每个服务最多保留 3 个文件。

## 7. 更新项目

更新全部服务：

```bash
git pull
docker compose --env-file .env.production up -d --build
docker compose --env-file .env.production ps
```

只更新后端源码或 Python 依赖：

```bash
docker compose --env-file .env.production build backend resume-worker knowledge-worker
docker compose --env-file .env.production up -d --force-recreate \
  backend resume-worker knowledge-worker
```

只更新前端：

```bash
docker compose --env-file .env.production build frontend
docker compose --env-file .env.production up -d --force-recreate frontend
```

修改后端相关环境变量后，至少重新创建后端和两个 Worker：

```bash
docker compose --env-file .env.production up -d --force-recreate \
  backend resume-worker knowledge-worker
```

## 8. 数据备份

创建备份目录：

```bash
mkdir -p backups
```

备份 MySQL：

```bash
docker compose --env-file .env.production exec -T mysql \
  sh -c 'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' \
  > "backups/ai_interview_$(date +%F_%H%M%S).sql"
```

检查备份文件：

```bash
ls -lh backups/
```

Qdrant 保存知识库向量索引。重要环境应同时备份 `qdrant_data` 卷或使用 Qdrant Snapshot；使用 OSS 时，还需要单独保障知识库原文件的备份与生命周期策略。

## 9. 管理员设置

先通过网页正常注册用户，再按邮箱提升为管理员：

```bash
docker compose --env-file .env.production exec backend \
  python scripts/promote_admin.py "admin@example.com"
```

重新登录后即可进入知识库管理页面。该脚本不会创建带默认弱密码的管理员账号。

## 10. HTTPS 部署

建议在应用前配置 Caddy、Traefik 或宿主机 Nginx，并在 `.env.production` 中设置：

```env
HTTP_BIND=127.0.0.1
HTTP_PORT=8080
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SAMESITE=lax
```

然后将域名反向代理到 `http://127.0.0.1:8080`，并配置有效 TLS 证书。代理需要保留 `Host`、`X-Forwarded-For` 和 `X-Forwarded-Proto` 请求头。

## 11. 常见排查

Compose 配置无效：

```bash
docker compose --env-file .env.production config
```

后端没有就绪：

```bash
docker compose --env-file .env.production ps
docker compose --env-file .env.production logs --tail=200 backend mysql rabbitmq qdrant
```

检查 RabbitMQ：

```bash
docker compose --env-file .env.production exec rabbitmq rabbitmq-diagnostics -q ping
```

检查 Qdrant：

```bash
docker compose --env-file .env.production exec backend \
  curl -fsS http://qdrant:6333/collections
```

检查 MySQL 关键限制：

```bash
docker compose --env-file .env.production exec backend \
  python scripts/check_mysql_limits.py
```

更换 Embedding 模型或向量维度后，需要使用知识库重建功能重新生成 Qdrant 索引，不能继续混用旧向量。
