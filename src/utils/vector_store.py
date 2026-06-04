"""GameForge - 向量存储模块

基于Qdrant的代码向量检索，支持：
- 存储生成过的代码片段（自动向量化）
- 检索相似代码作为LLM生成的参考（RAG）
- 按任务类型、引擎类型过滤

Qdrant不可用时自动降级为无检索模式，不影响主流程。
"""

import os
import logging
import hashlib
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("GameForge.vector")

# 全局客户端实例
_qdrant_client = None
_qdrant_available = None  # None=未检测, True/False=已检测

COLLECTION_NAME = "gameforge_code"
EMBEDDING_SIZE = 1536  # text-embedding-3-small 维度


def _get_embedding_func():
    """获取嵌入函数（使用OpenAI兼容API）"""
    from openai import OpenAI

    # 从环境变量读取嵌入模型配置
    base_url = os.getenv("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
    api_key = os.getenv("MIMO_API_KEY", "")

    # DeepSeek 也可以用作嵌入
    if not api_key:
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        api_key = os.getenv("DEEPSEEK_API_KEY", "")

    client = OpenAI(api_key=api_key, base_url=base_url)

    def embed(texts: List[str]) -> List[List[float]]:
        """生成文本嵌入向量"""
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=texts,
        )
        return [item.embedding for item in response.data]

    return embed


async def get_qdrant():
    """获取Qdrant客户端实例（延迟初始化，不可用时返回None）"""
    global _qdrant_client, _qdrant_available

    if _qdrant_available is False:
        return None

    if _qdrant_client is None:
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams
            import yaml

            # 优先从 config.yaml 读取，回退到环境变量
            host = os.getenv("QDRANT_HOST", "")
            port = os.getenv("QDRANT_PORT", "")
            api_key = os.getenv("QDRANT_API_KEY", "")

            if not host:
                try:
                    config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "config.yaml")
                    with open(config_path, "r", encoding="utf-8") as f:
                        config = yaml.safe_load(f)
                    qdrant_cfg = config.get("vector_db", {})
                    host = qdrant_cfg.get("host", "localhost")
                    port = str(qdrant_cfg.get("port", 6333))
                except Exception:
                    host, port = "localhost", "6333"

            host = host or "localhost"
            port = int(port or 6333)
            if api_key and api_key.startswith("your_"):
                api_key = None

            _qdrant_client = QdrantClient(
                host=host,
                port=port,
                api_key=api_key or None,
                timeout=5,
            )

            # 测试连接 + 确保 collection 存在
            collections = _qdrant_client.get_collections().collections
            collection_names = [c.name for c in collections]

            if COLLECTION_NAME not in collection_names:
                _qdrant_client.create_collection(
                    collection_name=COLLECTION_NAME,
                    vectors_config=VectorParams(
                        size=EMBEDDING_SIZE,
                        distance=Distance.COSINE,
                    ),
                )
                logger.info(f"创建Qdrant集合: {COLLECTION_NAME}")
            else:
                logger.info(f"Qdrant集合已存在: {COLLECTION_NAME}")

            _qdrant_available = True
            logger.info(f"Qdrant连接成功: {host}:{port}")

        except ImportError:
            logger.warning("qdrant-client包未安装，向量检索禁用。运行: pip install qdrant-client")
            _qdrant_available = False
            return None
        except Exception as e:
            logger.warning(f"Qdrant连接失败，向量检索禁用: {e}")
            _qdrant_available = False
            return None

    return _qdrant_client


async def store_code(
    code: str,
    file_path: str,
    task_name: str,
    engine: str = "unity",
    task_type: str = "code",
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """存储代码片段到向量库

    Args:
        code: 代码内容
        file_path: 文件路径
        task_name: 任务名称（如 "PlayerController"）
        engine: 游戏引擎（unity/unreal）
        task_type: 任务类型（code/test）
        metadata: 额外元数据

    Returns:
        是否存储成功
    """
    client = await get_qdrant()
    if client is None:
        return False

    try:
        from qdrant_client.models import PointStruct

        # 生成嵌入向量
        embed = _get_embedding_func()
        # 用任务名+代码前500字符作为嵌入输入
        embed_input = f"{task_name}\n{code[:500]}"
        vectors = embed([embed_input])

        # 生成唯一ID
        point_id = hashlib.md5(f"{file_path}:{task_name}".encode()).hexdigest()[:32]

        # 存储
        payload = {
            "file_path": file_path,
            "task_name": task_name,
            "engine": engine,
            "task_type": task_type,
            "code_preview": code[:1000],
            **(metadata or {}),
        }

        client.upsert(
            collection_name=COLLECTION_NAME,
            points=[
                PointStruct(
                    id=point_id,
                    vector=vectors[0],
                    payload=payload,
                )
            ],
        )

        logger.debug(f"代码已存入向量库: {file_path}")
        return True

    except Exception as e:
        logger.warning(f"向量存储失败: {e}")
        return False


async def search_similar_code(
    query: str,
    engine: str = "unity",
    task_type: str = "code",
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """检索相似代码片段

    Args:
        query: 查询文本（任务描述或代码片段）
        engine: 过滤引擎类型
        task_type: 过滤任务类型
        limit: 返回数量

    Returns:
        相似代码列表，每项包含 file_path, task_name, code_preview, score
    """
    client = await get_qdrant()
    if client is None:
        return []

    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        # 生成查询向量
        embed = _get_embedding_func()
        query_vector = embed([query])[0]

        # 构建过滤条件
        must_conditions = [
            FieldCondition(key="engine", match=MatchValue(value=engine)),
            FieldCondition(key="task_type", match=MatchValue(value=task_type)),
        ]

        # 检索
        results = client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            query_filter=Filter(must=must_conditions),
            limit=limit,
            with_payload=True,
        )

        return [
            {
                "file_path": hit.payload.get("file_path", ""),
                "task_name": hit.payload.get("task_name", ""),
                "code_preview": hit.payload.get("code_preview", ""),
                "score": hit.score,
            }
            for hit in results.points
        ]

    except Exception as e:
        logger.warning(f"向量检索失败: {e}")
        return []


async def get_collection_stats() -> Dict[str, Any]:
    """获取向量库统计信息"""
    client = await get_qdrant()
    if client is None:
        return {"available": False}

    try:
        info = client.get_collection(COLLECTION_NAME)
        return {
            "available": True,
            "collection": COLLECTION_NAME,
            "points_count": info.points_count if hasattr(info, 'points_count') else 0,
            "status": str(info.status) if hasattr(info, 'status') else "unknown",
        }
    except Exception as e:
        return {"available": False, "error": str(e)}
