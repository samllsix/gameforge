"""GameForge - 操作结果统一契约。

借鉴 GameFactory-3A（OpenDCAI）适配器的结果信封：凡是"跑外部进程 /
产出证据"的操作（运行时冒烟、playtest、视觉审查、引擎安装……），
返回值必须携带同一组信封字段，调用方与测试不再各自发明 dict 形态。

契约（schema ``gameforge.operation_result.v1``），信封键与操作特有键
平铺在同一层（不嵌套 payload，方便既有消费方按键取值）::

    {
      "schema": "gameforge.operation_result.v1",
      "operation": "playtest.run",      # 操作标识 <模块>.<动作>
      "ok": True,                       # 业务通过与否（成功判定由模块用
                                        #   原生产物得出，退出码 0 不算依据）
      "skipped": False,                 # 因环境/配置未执行（不是失败）
      "skip_reason": None,              # skipped=True 时必填，否则必须为 None
      "project_id": "gf_xxx",           # run 级操作的产物编址（模块级约定）
      "run_id": "20260906_120000",
      "artifacts": {"report": "..."},   # 本操作落盘的产物 {名称: 路径}
      "warnings": ["..."],              # 非致命问题（如降级、回退）
      "errors": ["..."],                # 致命问题清单（ok=False 的机器可读原因）
      ...操作特有键（report/checks/issues/runnable...）
    }

约定：
- ``ok=False``（且未 skipped）时 ``errors`` 必须非空——失败必须可归因；
- ``errors`` 元素为 str 或结构化 dict（运行时冒烟保留 pattern/snippet 结构）；
- ``findings`` 是 playtest 的历史别名，与 ``errors`` 指向同一列表对象，
  新代码一律读写 ``errors``。

本模块零第三方依赖、不导入任何业务模块，可从任何地方安全导入。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

OPERATION_RESULT_SCHEMA = "gameforge.operation_result.v1"


def new_operation_result(
    operation: str,
    *,
    ok: bool = False,
    skipped: bool = False,
    skip_reason: Optional[str] = None,
    project_id: Optional[str] = None,
    run_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造一份符合契约的操作结果底座。

    Args:
        operation: 操作标识，形如 ``playtest.run``。
        ok: 业务是否通过。
        skipped: 是否因环境/配置未执行。
        skip_reason: 跳过原因；skipped=True 时必填。
        project_id / run_id: 产物编址。
        extra: 操作特有键，平铺合并进结果（不覆盖信封键）。

    Returns:
        契约结果 dict；``artifacts``/``warnings``/``errors`` 已初始化为空。
    """
    result: Dict[str, Any] = {
        "schema": OPERATION_RESULT_SCHEMA,
        "operation": operation,
        "ok": bool(ok),
        "skipped": bool(skipped),
        "skip_reason": skip_reason,
        "project_id": project_id,
        "run_id": run_id,
        "artifacts": {},
        "warnings": [],
        "errors": [],
    }
    envelope = set(result)
    for key, value in (extra or {}).items():
        if key in envelope:
            raise ValueError(f"extra 键与信封键冲突: {key}")
        result[key] = value
    return result


def add_artifact(result: Dict[str, Any], name: str, path: Union[str, Path]) -> str:
    """登记一个本操作落盘的产物，返回其字符串路径（方便直接使用）。"""
    text = str(path)
    result["artifacts"][name] = text
    return text


def validate_operation_result(result: Any) -> List[str]:
    """校验契约，返回违例清单（空列表 = 合规）。

    只做结构校验，不做业务判断；任何不是 dict 的输入视为整体违例。
    """
    if not isinstance(result, dict):
        return [f"结果不是 dict: {type(result).__name__}"]
    issues: List[str] = []

    if result.get("schema") != OPERATION_RESULT_SCHEMA:
        issues.append(f"schema 应为 {OPERATION_RESULT_SCHEMA}")
    if not result.get("operation"):
        issues.append("operation 缺失或为空")

    ok = result.get("ok")
    skipped = result.get("skipped")
    if not isinstance(ok, bool):
        issues.append("ok 必须是 bool")
    if not isinstance(skipped, bool):
        issues.append("skipped 必须是 bool")

    skip_reason = result.get("skip_reason")
    if skipped and not skip_reason:
        issues.append("skipped=True 时必须给出 skip_reason")
    if not skipped and skip_reason is not None:
        issues.append("skipped=False 时 skip_reason 必须为 None")

    for key in ("errors", "warnings"):
        if not isinstance(result.get(key), list):
            issues.append(f"{key} 必须是 list")
    if not isinstance(result.get("artifacts"), dict):
        issues.append("artifacts 必须是 dict")

    errors = result.get("errors")
    if isinstance(errors, list) and isinstance(ok, bool) and isinstance(skipped, bool):
        if ok is False and skipped is False and not errors:
            issues.append("ok=False 且未跳过时 errors 必须非空（失败必须可归因）")

    # 注：产物按 (project_id, run_id) 编址是 run 级操作（playtest/视觉审查）的
    # 模块级约定，不在此强制——安装器等全局操作也有合法 artifacts。
    return issues


def is_contract_valid(result: Any) -> bool:
    """契约是否合规（validate_operation_result 的便捷布尔形式）。"""
    return not validate_operation_result(result)
