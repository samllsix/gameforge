"""GameForge - AI Image Generation Test

测试 Step 和 SenseNova 图像生成 API。
"""

import asyncio
import os
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 加载 .env 文件
env_file = project_root / ".env"
if env_file.exists():
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


async def test_step_api():
    """测试 Step Image Edit 2 API"""
    print("\n=== 测试 Step Image Edit 2 API ===")
    
    api_key = os.getenv("STEP_API_KEY")
    base_url = os.getenv("STEP_BASE_URL", "https://api.stepfun.com/step_plan/v1")
    
    if not api_key:
        print("[SKIP] 未设置 STEP_API_KEY")
        return
    
    print(f"API Key: {api_key[:10]}...")
    print(f"Base URL: {base_url}")
    
    try:
        import httpx
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        # 尝试不同的端点
        endpoints = [
            "/images/generations",
            "/v1/images/generations",
            "/image/generate",
            "/api/v1/images/generations",
        ]
        
        payload = {
            "model": "step-image-edit-2",
            "prompt": "a cute cat sitting on a windowsill, digital art",
            "size": "1024x1024",
            "n": 1,
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            for endpoint in endpoints:
                print(f"\n  尝试端点: {endpoint}")
                try:
                    response = await client.post(
                        f"{base_url}{endpoint}",
                        headers=headers,
                        json=payload,
                    )
                    print(f"  状态码: {response.status_code}")
                    if response.status_code == 200:
                        print(f"  [OK] 成功!")
                        data = response.json()
                        print(f"  响应: {str(data)[:200]}...")
                        break
                    else:
                        print(f"  响应: {response.text[:200]}")
                except Exception as e:
                    print(f"  错误: {e}")
    
    except Exception as e:
        print(f"[ERROR] 测试失败: {e}")


async def test_sensenova_api():
    """测试 SenseNova U1.5 Lite API"""
    print("\n=== 测试 SenseNova U1.5 Lite API ===")
    
    api_key = os.getenv("SENSENOVA_API_KEY")
    base_url = os.getenv("SENSENOVA_BASE_URL", "https://token.sensenova.cn/v1")
    
    if not api_key:
        print("[SKIP] 未设置 SENSENOVA_API_KEY")
        return
    
    print(f"API Key: {api_key[:10]}...")
    print(f"Base URL: {base_url}")
    
    try:
        import httpx
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        # 尝试不同的端点和模型
        endpoints = [
            "/images/generations",
            "/v1/images/generations",
        ]
        
        model_names = [
            "U1.5-Lite",
            "senseimage-2",
            "image",
            "text-to-image",
        ]
        
        base_payload = {
            "prompt": "a futuristic cityscape with neon lights, cyberpunk style",
            "size": "1024x1024",
            "n": 1,
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            for endpoint in endpoints:
                for model in model_names:
                    print(f"\n  尝试端点: {endpoint}, 模型: {model}")
                    test_payload = {**base_payload, "model": model}
                    try:
                        response = await client.post(
                            f"{base_url}{endpoint}",
                            headers=headers,
                            json=test_payload,
                        )
                        print(f"  状态码: {response.status_code}")
                        if response.status_code == 200:
                            print(f"  [OK] 成功!")
                            data = response.json()
                            print(f"  响应: {str(data)[:200]}...")
                            break
                        else:
                            print(f"  响应: {response.text[:200]}")
                    except Exception as e:
                        print(f"  错误: {e}")
                else:
                    continue
                break
    
    except Exception as e:
        print(f"[ERROR] 测试失败: {e}")


async def main():
    """运行所有测试"""
    print("GameForge AI Image Generation Test")
    print("=" * 60)
    
    await test_step_api()
    await test_sensenova_api()
    
    print("\n" + "=" * 60)
    print("测试完成!")


if __name__ == "__main__":
    asyncio.run(main())
