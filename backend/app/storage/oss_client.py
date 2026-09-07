from datetime import datetime, UTC
from pathlib import Path
from uuid import uuid4

import alibabacloud_oss_v2 as oss
import alibabacloud_oss_v2.aio as oss_aio
from fastapi import HTTPException, status

from app.core.config import settings


class OssObjectStorage:
    def __init__(self) -> None:
        """检查对象存储区域和存储桶配置，缺失时抛出服务配置错误。"""
        if not settings.aliyun_oss_region or not settings.aliyun_oss_bucket:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Aliyun OSS is not configured. Set ALIYUN_OSS_REGION and ALIYUN_OSS_BUCKET.",
            )

    async def upload_bytes(self, data: bytes, key: str) -> str:
        """上传字节内容到指定对象键，返回对象键并确保关闭客户端。"""
        client = self._create_client()
        try:
            await client.put_object(
                oss.PutObjectRequest(
                    bucket=settings.aliyun_oss_bucket,
                    key=key,
                    body=data,
                )
            )
            return key
        finally:
            await client.close()

    async def download_bytes(self, key: str) -> bytes:
        """下载对象内容，兼容同步或异步读取响应体并确保关闭客户端。"""
        client = self._create_client()
        try:
            result = await client.get_object(oss.GetObjectRequest(bucket=settings.aliyun_oss_bucket, key=key))
            body = result.body
            if hasattr(body, "read"):
                data = body.read()
                if hasattr(data, "__await__"):
                    data = await data
                return data
            raise RuntimeError("OSS response body is not readable")
        finally:
            await client.close()

    def _create_client(self):
        """使用环境变量凭据及配置的区域和端点创建异步存储客户端。"""
        credentials_provider = oss.credentials.EnvironmentVariableCredentialsProvider()
        cfg = oss.config.load_default()
        cfg.credentials_provider = credentials_provider
        cfg.region = settings.aliyun_oss_region
        if settings.aliyun_oss_endpoint:
            cfg.endpoint = settings.aliyun_oss_endpoint
        return oss_aio.AsyncClient(cfg)


def build_knowledge_oss_key(kind: str, filename: str) -> str:
    """按文件用途、年月及随机标识生成知识文件的对象存储键。"""
    now = datetime.now(UTC)
    safe_filename = _safe_filename(filename)
    prefix = settings.aliyun_oss_prefix.rstrip("/") or "knowledge"
    return f"{prefix}/{kind}/{now:%Y/%m}/{uuid4().hex}-{safe_filename}"


def _safe_filename(filename: str) -> str:
    """提取文件名并替换路径分隔符，空文件名使用默认名称。"""
    name = Path(filename or "uploaded").name.replace("\\", "_").replace("/", "_")
    return name or "uploaded"
