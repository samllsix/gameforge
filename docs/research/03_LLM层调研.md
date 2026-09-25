# M3 · LLM 层调研

> **调研范围**：`src/utils/llm_client.py`（611 行）+ `config/config.yaml` 的 `llm:` 段 + `src/utils/llm_health.py`。
>
> **一句话结论**：这层的**配置层设计良好**（多 provider 注册、按角色分模型、Key 不进版本库、重试/熔断/缓存/stub 齐全），但**调用层有三个防护漏洞**：不区分错误类型一律重试、不检测空回复、两条路径保护不对等。表现为"配错 Key 要卡十几秒、模型不出力也看不出来"。

---

## 1. 对接链路

```
Agent（self.llm.chat(...)）
  ↓
LLMClient.chat()                     utils/llm_client.py:239
  ├─ 缓存查询（仅 temperature=0，走 Redis）
  ├─ 熔断检查 can_execute()
  └─ for attempt in range(4):                    ← 第 1 层重试
        └─ AsyncOpenAI(max_retries=3).chat.completions.create()
                                                 ← 第 2 层重试（SDK 内部）
  ↓
LLMClientPool.get_client(base_url, api_key)      :136  按 url|key 缓存实例
```

配置解析：`_resolve_provider_config()` :550 按 provider 名查 `providers[name].base_url` + `os.getenv(api_key_env)`；`get_llm_client()` :578 按 `provider + base_url` 缓存客户端实例。

## 2. 问题清单

| 编号 | 问题 | 严重度 | 状态 |
|---|---|---|---|
| M3-01 | **两层重试叠加 + 不区分错误类型**：`chat()` 4 轮 × SDK 3 轮 = 最坏 16 次请求；**401/403/404 这类永久错误也重试**，配错 Key 要卡十几秒才报错 | **高** | 未处理 |
| M3-02 | **空回复静默吞掉**：`step-3.7-flash` 在 `max_tokens` 偏小时返回 HTTP 200 + 空正文，代码直接 `return ""` → Agent 侧 `_extract_json("")` 得 None → 静默走模板降级。**假通过**：看着生成了，实际 LLM 没出力 | **高** | 未处理 |
| M3-03 | 熔断对永久错误也计数（前 5 次调用各自重试 4 轮 = 20 次失败后熔断 60s，表现为"先慢后瞬间全失败"） | 中 | 未处理（随 M3-01 一并解决） |
| M3-04 | `chat_sync()`（同步路径，`art_director.py:212` 在用）**无熔断、无缓存、无指标**，供应商挂掉时裸重试 4 轮 + 退避 | 中 | 未处理 |

## 3. 测试到的事实（非推断）

1. **Key 有效性**：`stepfun` Key 实测可用，`GET /models` 返回 10 个模型；`step-5-preview` 10.6s 返回 1554 字合法 JSON，`reasoning_tokens=0`，是当前最佳选型。
2. **空回复实测复现**：`step-3.7-flash` `max_tokens=120` → HTTP 200、`completion_tokens=120` 吃满、**正文为空**；`step-3.5-flash-2603` 同样空（533 tokens 全吃掉）。原因是推理型模型把 token 花在思考上。
3. **模型名必须匹配**：`step-2-16k` / `step-1-8k` 等服务端不存在，返回 404 `The model ... does not exist`。
4. **stepfun 支持 ```json 围栏**：JSON 提取链路需具备剥围栏能力（`src/utils/json_extractor.py` 已有）。

## 4. 设计好但未启用的能力

| 能力 | 现状 |
|---|---|
| 按角色分配模型（15 个 `llm.models.<agent>` 槽位） | 已全部切到 stepfun/step-5-preview，能力闲置；后续可用于控成本 |
| 多模态 `chat_with_images()` :336 | 实现完整，但 `visual_review.enabled: false` 且 `visual_reviewer` 槽位缺失，链路跑不通 |
| LLM 结果缓存 | 仅 `temperature=0` 启用，而主链路各 Agent 是 0.1~0.4，**几乎不命中** |
| `src/adapters/` 整套 | 面向游戏内 AI 控制器的另一套实现，主链路零引用，空转资产 |

## 5. 修复方案（待批准，尚未实施）

> 说明：以下方案本次调研期间**曾实现过一版并通过 8 个回归测试**，但因未经批准即改动生产代码，
> 已整体回滚（改动前的文件备份在 `/tmp/gf_llm_client_modified.bak`）。此处仅作为提案留存，
> 供后续按流程审批后再实施。

**M3-01 / M3-03**：新增 `_is_retryable(exc)` 错误分类器，基于 `openai` SDK 异常类型判断（非字符串匹配）：

| 类别 | 异常类型 | 处理 |
|---|---|---|
| 永久错误 | `AuthenticationError`(401) / `PermissionDeniedError`(403) / `NotFoundError`(404) / `BadRequestError`(400) | **不重试、不退避**，立即抛 |
| 可重试 | `RateLimitError`(429) / `InternalServerError`(5xx) / `APITimeoutError` / `APIConnectionError` | 指数退避重试 |

**M3-02**：`chat()` 收到空白回复时记 `warning` 日志（含 model / max_tokens / tokens 用量）、计入失败、按可重试处理；重试耗尽后抛出带明确原因的异常，让 Agent 的 `except` 分支走降级时**日志里有据可查**。

**M3-04**：`chat_sync()` 补上熔断检查与错误分类，与异步路径保护对等。

## 附：验证方式

```bash
grep -n "_is_retryable\|EmptyResponse\|circuit_breaker" src/utils/llm_client.py | head
.venv/bin/python -m pytest tests/unit/test_llm_client.py -q
curl -s https://api.stepfun.com/step_plan/v1/models -H "Authorization: Bearer $STEPFUN_API_KEY"
```
