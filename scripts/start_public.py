"""一键启动 GameForge 公网访问

支持三种隧道模式，自动选择可用方案：
1. localhost.run (SSH 隧道，无需安装)
2. bore.pub (Rust 工具，需下载)
3. ngrok (需安装 + 认证)
"""

import subprocess
import sys
import time
import os
import re
import threading

PORT = 8001


def start_server():
    """启动 FastAPI 服务器"""
    print(f"[1/2] 启动 FastAPI 服务器 (端口 {PORT})...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.api.main"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    time.sleep(3)
    if proc.poll() is not None:
        print("[ERROR] 服务器启动失败")
        return None
    print(f"[OK] 服务器已启动 http://localhost:{PORT}")
    return proc


def try_localhost_run():
    """使用 localhost.run (SSH 隧道，无需安装任何东西)"""
    print("\n[2/2] 启动 localhost.run 公网隧道...")
    proc = subprocess.Popen(
        ["ssh", "-o", "StrictHostKeyChecking=no",
         "-R", f"80:localhost:{PORT}", "nokey@localhost.run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    url = None
    start = time.time()
    while time.time() - start < 15:
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.5)
            continue
        text = line.decode("utf-8", errors="replace").strip()
        if text:
            print(f"  {text}")
        # 查找 URL
        match = re.search(r'(https?://[\w.-]+\.lhr\.life[^\s]*)', text)
        if not match:
            match = re.search(r'(https?://[\w.-]+\.localhost\.run[^\s]*)', text)
        if match:
            url = match.group(1)
            break

    return proc, url


def print_success(url):
    """打印成功信息"""
    print(f"\n{'=' * 60}")
    print(f"  公网访问地址: {url}")
    print(f"{'=' * 60}")
    print(f"\n  API 文档:     {url}/docs")
    print(f"  Web 界面:     {url}/app")
    print(f"  健康检查:     {url}/health")
    print(f"\n  分享此链接给任何人即可访问")
    print(f"  按 Ctrl+C 停止服务")
    print(f"{'=' * 60}")


def main():
    print("=" * 60)
    print("  GameForge 公网部署")
    print("=" * 60)

    server_proc = start_server()
    if not server_proc:
        return

    tunnel_proc = None
    public_url = None

    try:
        # 方案1: localhost.run
        tunnel_proc, public_url = try_localhost_run()

        if public_url:
            print_success(public_url)
        else:
            print("\n[WARN] localhost.run 未获取到 URL")
            print("请手动尝试以下方案之一:")
            print(f"  1. ssh -R 80:localhost:{PORT} nokey@localhost.run")
            print(f"  2. 安装 ngrok: ngrok http {PORT}")
            print(f"  3. 本地访问: http://localhost:{PORT}")

        # 保持运行
        try:
            while True:
                time.sleep(1)
                # 检查隧道是否还活着
                if tunnel_proc and tunnel_proc.poll() is not None:
                    print("\n[WARN] 隧道连接断开，正在重连...")
                    tunnel_proc, public_url = try_localhost_run()
                    if public_url:
                        print_success(public_url)
        except KeyboardInterrupt:
            print("\n\n正在停止服务...")

    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        if tunnel_proc:
            tunnel_proc.terminate()
        server_proc.terminate()
        server_proc.wait(timeout=5)
        print("[OK] 服务已停止")


if __name__ == "__main__":
    main()
