"""GameForge - 图像生成模块

本目录实现 Image MCP 的 provider 链：
- procedural/        : 纯算法生成（无 GPU 依赖，本地主力）
- providers/         : SDXL-Turbo / ComfyUI / StableHorde 等 provider 客户端
- cache/             : 语义 hash 兜底
"""