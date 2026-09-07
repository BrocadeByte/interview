import asyncio
import sys
from pathlib import Path

from sqlalchemy import text


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import engine


async def check_limits() -> None:
    """查询 MySQL 报文大小、连接空闲超时及知识正文字段类型，结束后释放连接池。"""
    try:
        async with engine.begin() as conn:
            rows = (await conn.execute(text("SHOW VARIABLES WHERE Variable_name IN ('max_allowed_packet', 'wait_timeout')"))).fetchall()
            print({row[0]: row[1] for row in rows})
            column = (await conn.execute(text("SHOW COLUMNS FROM knowledge_documents LIKE 'content'"))).fetchone()
            print({"knowledge_documents.content": column[1] if column else None})
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(check_limits())
