"""GameForge - AI Image Client

支持多个 AI 图像生成 API：
- Step Image Edit 2 (stepfun.com)
- SenseNova U1.5 Lite (sensenova.cn)

降级链：
AI API → 程序化生成（ProceduralSpriteGenerator）
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _find_repo_root() -> str:
    cwd = os.getcwd()
    if os.path.isdir(os.path.join(cwd, "src", "image")):
        return cwd
    here = os.path.dirname(os.path.abspath(__file__))
    cur = here
    for _ in range(6):
        if os.path.isdir(os.path.join(cur, "src", "image")):
            return cur
        cur = os.path.dirname(cur)
    return cwd


_repo_root = _find_repo_root()
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)


@dataclass
class ImageResult:
    """图像生成结果"""
    success: bool
    image_path: Optional[str] = None
    image_base64: Optional[str] = None
    prompt: str = ""
    size: Tuple[int, int] = (512, 512)
    seed: Optional[int] = None
    provider: str = ""
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "success": self.success,
            "prompt": self.prompt,
            "size": list(self.size),
            "provider": self.provider,
        }
        if self.image_path:
            result["image_path"] = self.image_path
        if self.image_base64:
            result["image_base64"] = self.image_base64[:200] + "..."
        if self.seed is not None:
            result["seed"] = self.seed
        if self.error:
            result["error"] = self.error
        if self.metadata:
            result["metadata"] = self.metadata
        return result


class BaseImageProvider(ABC):
    """图像生成提供者基类"""

    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    @abstractmethod
    async def generate_image(
        self,
        prompt: str,
        size: Tuple[int, int] = (512, 512),
        seed: Optional[int] = None,
        **kwargs: Any,
    ) -> ImageResult:
        """生成图像"""
        ...

    def _save_image(
        self, image_bytes: bytes, prompt: str, output_dir: str
    ) -> str:
        """保存图像到文件"""
        from PIL import Image
        import io

        os.makedirs(output_dir, exist_ok=True)

        seed = abs(hash(prompt)) % (2**32)
        timestamp = int(time.time())
        filename = f"ai_{seed}_{timestamp}.png"
        filepath = os.path.join(output_dir, filename)

        image = Image.open(io.BytesIO(image_bytes))
        image.save(filepath)

        return filepath


class StepImageProvider(BaseImageProvider):
    """Step Image Edit 2 API 提供者"""

    # Step API 支持的有效尺寸
    SUPPORTED_SIZES = [
        (1024, 1024),
        (768, 1360),
        (896, 1184),
        (1360, 768),
        (1184, 896),
    ]

    def __init__(self, api_key: str, base_url: str = "https://api.stepfun.com/step_plan/v1"):
        super().__init__(api_key, base_url)

    def _find_closest_size(self, size: Tuple[int, int]) -> Tuple[int, int]:
        """找到最接近的有效尺寸"""
        w, h = size
        # 检查是否已经是有效尺寸
        if (w, h) in self.SUPPORTED_SIZES:
            return (w, h)
        
        # 找到最接近的有效尺寸
        min_diff = float('inf')
        best_size = self.SUPPORTED_SIZES[0]
        
        for sw, sh in self.SUPPORTED_SIZES:
            diff = abs(sw - w) + abs(sh - h)
            if diff < min_diff:
                min_diff = diff
                best_size = (sw, sh)
        
        return best_size

    async def generate_image(
        self,
        prompt: str,
        size: Tuple[int, int] = (512, 512),
        seed: Optional[int] = None,
        output_dir: str = r"D:\comfyui\output",
        **kwargs: Any,
    ) -> ImageResult:
        """使用 Step Image Edit 2 API 生成图像"""
        try:
            import httpx

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            # 使用正确的端点
            endpoint = "/images/generations"
            
            # 调整尺寸到有效值
            actual_size = self._find_closest_size(size)
            size_str = f"{actual_size[0]}x{actual_size[1]}"

            payload = {
                "model": "step-image-edit-2",
                "prompt": prompt,
                "size": size_str,
                "n": 1,
            }

            if seed is not None:
                payload["seed"] = seed

            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.base_url}{endpoint}",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

            image_data = data["data"][0]
            image_b64 = image_data.get("b64_json") or image_data.get("url", "")

            if image_b64 and image_b64.startswith("http"):
                async with httpx.AsyncClient(timeout=60.0) as client:
                    img_resp = await client.get(image_b64)
                    img_resp.raise_for_status()
                    image_bytes = img_resp.content
            else:
                image_bytes = base64.b64decode(image_b64)

            image_path = self._save_image(image_bytes, prompt, output_dir)

            return ImageResult(
                success=True,
                image_path=image_path,
                image_base64=image_b64,
                prompt=prompt,
                size=actual_size,
                seed=seed,
                provider="step-image-edit-2",
                metadata={
                    "model": "step-image-edit-2",
                    "requested_size": size,
                    "actual_size": actual_size,
                    "api_response": data,
                },
            )

        except Exception as e:
            return ImageResult(
                success=False,
                prompt=prompt,
                size=size,
                provider="step-image-edit-2",
                error=str(e),
            )


class SenseNovaImageProvider(BaseImageProvider):
    """SenseNova U1.5 Lite API 提供者"""

    # 尝试不同的模型名称
    MODEL_NAMES = [
        "U1.5-Lite",
        "U1.5-Lite-image",
        "senseimage-2",
        "image",
        "text-to-image",
    ]

    def __init__(self, api_key: str, base_url: str = "https://token.sensenova.cn/v1"):
        super().__init__(api_key, base_url)

    async def generate_image(
        self,
        prompt: str,
        size: Tuple[int, int] = (512, 512),
        seed: Optional[int] = None,
        output_dir: str = r"D:\comfyui\output",
        **kwargs: Any,
    ) -> ImageResult:
        """使用 SenseNova API 生成图像"""
        try:
            import httpx

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            # 尝试不同的端点和模型
            endpoints = [
                "/images/generations",
                "/v1/images/generations",
            ]
            
            payload_base = {
                "prompt": prompt,
                "size": f"{size[0]}x{size[1]}",
                "n": 1,
            }
            
            if seed is not None:
                payload_base["seed"] = seed

            async with httpx.AsyncClient(timeout=60.0) as client:
                last_error = None
                data = None
                
                for endpoint in endpoints:
                    for model in self.MODEL_NAMES:
                        payload = {**payload_base, "model": model}
                        
                        try:
                            response = await client.post(
                                f"{self.base_url}{endpoint}",
                                headers=headers,
                                json=payload,
                            )
                            if response.status_code == 200:
                                data = response.json()
                                break
                            elif response.status_code not in (404, 400):
                                response.raise_for_status()
                            last_error = f"{endpoint}/{model}: {response.status_code}"
                        except httpx.HTTPStatusError as e:
                            last_error = f"{endpoint}/{model}: {e.response.status_code}"
                            if e.response.status_code not in (404, 400):
                                raise
                    
                    if data:
                        break
                
                if not data:
                    raise Exception(f"All endpoints/models failed. Last error: {last_error}")

            image_data = data["data"][0]
            image_b64 = image_data.get("b64_json") or image_data.get("url", "")

            if image_b64 and image_b64.startswith("http"):
                async with httpx.AsyncClient(timeout=60.0) as client:
                    img_resp = await client.get(image_b64)
                    img_resp.raise_for_status()
                    image_bytes = img_resp.content
            else:
                image_bytes = base64.b64decode(image_b64)

            image_path = self._save_image(image_bytes, prompt, output_dir)

            return ImageResult(
                success=True,
                image_path=image_path,
                image_base64=image_b64,
                prompt=prompt,
                size=size,
                seed=seed,
                provider="sensenova",
                metadata={
                    "model": model,
                    "api_response": data,
                },
            )

        except Exception as e:
            return ImageResult(
                success=False,
                prompt=prompt,
                size=size,
                provider="sensenova",
                error=str(e),
            )


class AIImageClient:
    """AI 图像生成客户端

    提供统一的接口，支持多个 AI 图像生成 API。
    当 API 调用失败时，自动降级到程序化生成。
    """

    def __init__(
        self,
        output_dir: str = r"D:\comfyui\output",
        step_api_key: Optional[str] = None,
        step_base_url: str = "https://api.stepfun.com/step_plan/v1",
        sensenova_api_key: Optional[str] = None,
        sensenova_base_url: str = "https://token.sensenova.cn/v1",
        prefer_provider: str = "step",
        fallback_to_procedural: bool = True,
    ):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

        # 未显式传 key 时从环境变量读取（与 create_default_client / ImageMCPServer 行为一致），
        # 否则直接构造会静默得到一个无 provider 的客户端、全部走程序化兜底
        if step_api_key is None:
            step_api_key = os.getenv("STEP_API_KEY", "")
        if step_base_url == "https://api.stepfun.com/step_plan/v1":
            step_base_url = os.getenv("STEP_BASE_URL", step_base_url)
        if sensenova_api_key is None:
            sensenova_api_key = os.getenv("SENSENOVA_API_KEY", "")
        if sensenova_base_url == "https://token.sensenova.cn/v1":
            sensenova_base_url = os.getenv("SENSENOVA_BASE_URL", sensenova_base_url)

        self.providers: Dict[str, BaseImageProvider] = {}

        if step_api_key:
            self.providers["step"] = StepImageProvider(
                api_key=step_api_key,
                base_url=step_base_url,
            )

        if sensenova_api_key:
            self.providers["sensenova"] = SenseNovaImageProvider(
                api_key=sensenova_api_key,
                base_url=sensenova_base_url,
            )

        self.prefer_provider = prefer_provider
        self.fallback_to_procedural = fallback_to_procedural

        # 程序化生成器作为兜底
        if fallback_to_procedural:
            try:
                from src.image.procedural.procedural_sprite_generator import (
                    ProceduralSpriteGenerator,
                )
                self._procedural = ProceduralSpriteGenerator(output_dir=output_dir)
            except Exception:
                self._procedural = None
        else:
            self._procedural = None

    def _run_async(self, coro):
        """安全地运行异步代码"""
        try:
            loop = asyncio.get_running_loop()
            # 如果已经在事件循环中，使用 nest_asyncio 或创建任务
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        except RuntimeError:
            # 没有运行的事件循环
            return asyncio.run(coro)

    def generate_image(
        self,
        prompt: str,
        size: Optional[List[int]] = None,
        seed: Optional[int] = None,
        provider: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """生成图像（同步接口）

        Args:
            prompt: 图像描述
            size: [width, height]
            seed: 随机种子
            provider: 指定提供者（step 或 sensenova），None 表示自动选择

        Returns:
            生成结果字典
        """
        from src.image.style import apply_art_style
        # 全局美术风格约束：所有 AI 生图统一为类星露谷 2D 像素风
        prompt = apply_art_style(prompt)

        if size is None:
            size = [512, 512]
        w, h = size[0], size[1]

        # 选择提供者
        provider_name = provider or self.prefer_provider
        provider_instance = self.providers.get(provider_name)

        if provider_instance is None and self.providers:
            provider_name = list(self.providers.keys())[0]
            provider_instance = self.providers[provider_name]

        # 尝试 AI API
        if provider_instance:
            try:
                coro = provider_instance.generate_image(
                    prompt=prompt,
                    size=(w, h),
                    seed=seed,
                    output_dir=self.output_dir,
                    **kwargs,
                )
                result = self._run_async(coro)
                if result.success:
                    return result.to_dict()
            except Exception as e:
                print(f"[ai-image] generation failed: {e}", file=sys.stderr)

        # 降级到程序化生成
        if self.fallback_to_procedural and self._procedural is not None:
            procedural_result = self._procedural.generate_image(
                prompt=prompt, size=size, seed=seed
            )
            procedural_result["provider"] = "procedural-fallback"
            return procedural_result

        return {
            "success": False,
            "error": "No image provider available",
            "prompt": prompt,
            "size": size,
        }

    def get_available_providers(self) -> List[str]:
        """获取可用的图像生成提供者"""
        return list(self.providers.keys())


def create_default_client() -> AIImageClient:
    """创建默认的 AI 图像客户端

    从环境变量读取配置：
    - STEP_API_KEY: Step API 密钥
    - STEP_BASE_URL: Step API 基础 URL
    - SENSENOVA_API_KEY: SenseNova API 密钥
    - SENSENOVA_BASE_URL: SenseNova API 基础 URL
    """
    step_api_key = os.getenv("STEP_API_KEY", "")
    step_base_url = os.getenv("STEP_BASE_URL", "https://api.stepfun.com/step_plan/v1")

    sensenova_api_key = os.getenv("SENSENOVA_API_KEY", "")
    sensenova_base_url = os.getenv("SENSENOVA_BASE_URL", "https://token.sensenova.cn/v1")

    output_dir = os.getenv("IMAGE_OUTPUT_DIR", r"D:\comfyui\output")

    return AIImageClient(
        output_dir=output_dir,
        step_api_key=step_api_key or None,
        step_base_url=step_base_url,
        sensenova_api_key=sensenova_api_key or None,
        sensenova_base_url=sensenova_base_url,
    )
