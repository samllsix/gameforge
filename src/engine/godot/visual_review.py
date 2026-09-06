"""GameForge - VLM 视觉审查。

借鉴 GameFactory-3A：截图/录像必须经视觉评审，"一张截图没报错"
不是证据。本模块把 playtest 抓到的帧均匀采样后交给多模态模型，
按失败模式清单打分（2D 游戏版）：

- 画面黑屏/纯色/空白（渲染失败）
- 精灵错位、穿模、贴图缺失（紫黑棋盘格）
- UI 文本不可读、溢出、错位
- 风格不统一（混入占位图形）
- 相机异常（穿墙、超出场景边界）

结果落盘 ``<project>/.gameforge/<run_id>/visual_review.json``，
高严重度问题会把 playtest 判为未通过，驱动有界修复循环。

配置::

    visual_review:
      enabled: false      # 默认关
      max_frames: 4       # 送审帧数（均匀采样）
    llm:
      models:
        visual_reviewer:  # 不配则回退默认 provider/model
          provider: zhipu
          model: glm-4v-plus
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

from src.core import paths

logger = structlog.get_logger()

VISUAL_REVIEW_SCHEMA = "gameforge.visual_review.v1"

#: 审查清单提示词（失败模式 → 审查点，2D 游戏版）
_CHECKLIST = """你是游戏画面质量审查员。以下是同一次试玩中按时间顺序均匀采样的游戏截图。
请逐张检查并列出所有发现，只报告有把握的问题。审查清单：

1. 渲染失败：黑屏、纯色空白、什么都不显示
2. 精灵问题：明显错位、悬空/陷入地面、贴图缺失（紫黑棋盘格）、朝向错误
3. UI 问题：文本不可读、溢出屏幕、重叠错位
4. 风格问题：混入与整体风格不符的占位图形/剪贴画
5. 相机问题：视角异常、画面越出场景边界

只输出 JSON：
{"overall_pass": true/false, "summary": "一句话总评",
 "issues": [{"severity": "high|medium|low", "area": "render|sprite|ui|style|camera",
             "description": "具体问题与所在截图编号", "frame_index": 0}]}
没有问题时 issues 为空数组。"""


def pick_frames(frame_names: List[str], max_frames: int) -> List[str]:
    """从帧列表中均匀采样最多 max_frames 张（含首尾）。"""
    if not frame_names:
        return []
    if len(frame_names) <= max_frames:
        return list(frame_names)
    step = (len(frame_names) - 1) / (max_frames - 1)
    picked: List[str] = []
    for i in range(max_frames):
        name = frame_names[round(i * step)]
        if name not in picked:  # round 可能撞帧，去重保序
            picked.append(name)
    return picked


class VisualReviewer:
    """把 playtest 帧交给多模态模型审查。"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        vr_cfg = self.config.get("visual_review", {}) or {}
        self.enabled: bool = bool(vr_cfg.get("enabled", False))
        self.max_frames: int = int(vr_cfg.get("max_frames", 4))
        llm_models = self.config.get("llm", {}).get("models", {})
        self.llm_config: Dict[str, Any] = llm_models.get("visual_reviewer", {})
        self.provider = self.llm_config.get("provider")
        self.model = self.llm_config.get("model")

    def available(self) -> tuple:
        """视觉审查是否可跑（provider 可解析且 key 非空）。"""
        from src.utils.llm_client import _resolve_provider_config

        base_url, api_key = _resolve_provider_config(self.config, self.provider)
        if not base_url or not api_key:
            return False, "视觉模型 provider 未配置或缺少 API key"
        return True, "OK"

    async def review(
        self,
        frames_dir: Path,
        frame_names: List[str],
        *,
        project_id: str,
        run_id: str,
        context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """审查 frames_dir 下的帧并落盘 visual_review.json。

        任何失败（未启用/无 key/模型异常）都降级为 skipped，
        不阻塞 playtest 主流程。
        """
        result: Dict[str, Any] = {
            "schema": VISUAL_REVIEW_SCHEMA,
            "project_id": project_id,
            "run_id": run_id,
            "ok": False,
            "skipped": False,
            "issues": [],
            "summary": "",
            "reviewed_frames": [],
        }
        if not self.enabled:
            result["skipped"] = True
            result["reason"] = "visual_review.disabled"
            return result

        ok_avail, why = self.available()
        if not ok_avail:
            result["skipped"] = True
            result["reason"] = why
            logger.warning("visual_review.skipped", reason=why)
            return result

        picked = pick_frames(frame_names, self.max_frames)
        if not picked:
            result["skipped"] = True
            result["reason"] = "no_frames"
            return result
        result["reviewed_frames"] = picked

        images = []
        for name in picked:
            images.append({
                "data": (frames_dir / name).read_bytes(),
                "mime": "image/png",
            })

        user_prompt = (
            "请严格按清单审查并输出 JSON。\n\n补充上下文：" + (context or "未提供额外上下文")
        )
        messages = [
            {"role": "system", "content": _CHECKLIST},
            {"role": "user", "content": user_prompt},
        ]
        try:
            from src.utils.json_extractor import extract_json_strict
            from src.utils.llm_client import get_llm_client

            llm = get_llm_client(self.config, provider=self.provider, model=self.model)
            response = await llm.chat_with_images(messages, images, temperature=0.1, max_tokens=2048)
            parsed = extract_json_strict(response)
        except Exception as e:  # noqa: BLE001
            result["skipped"] = True
            result["reason"] = f"视觉模型调用失败: {e}"
            logger.warning("visual_review.llm_failed", error=str(e))
            return result

        issues = parsed.get("issues") or []
        hard_issues = [i for i in issues if str(i.get("severity", "")).lower() == "high"]
        result["issues"] = issues
        result["summary"] = str(parsed.get("summary", ""))
        result["ok"] = bool(parsed.get("overall_pass")) and not hard_issues

        paths.write_json(paths.visual_review_path(project_id, run_id), result)
        logger.info(
            "visual_review.finished",
            project_id=project_id, ok=result["ok"],
            issues=len(issues), hard=len(hard_issues),
        )
        return result
