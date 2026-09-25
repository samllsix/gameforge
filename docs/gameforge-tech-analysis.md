# GameForge 技术实现分析报告

> 基于对 `D:\game_project` 源码的实际通读（非 README 复述）。所有结论均带文件:行号佐证。
> 分析时间：2026-09-15

---

## 0. 一句话结论

**LLM 只负责"出语义"，"出语法"的活全部交给确定性代码；LLM 一旦失灵，全链路自动降到模板而不是报错。**

这条主线上，真正决定成败的不是 6 个 Agent 本身，而是 Agent 之后的**四道验收闸门**（gd-guard → headless 编译 → 运行时冒烟 → playtest + VLM）。项目最有工程含量的部分集中在 `workflow.py` 的后处理段（`:1749-1815`）和 `scene_to_godot.py`。

---

## 1. 项目规模（实测）

| 部分 | 文件数 | 行数 |
|------|--------|------|
| Python 源码 `src/` | 124 | 30,003 |
| 测试 `tests/` | 59 | 7,694 |
| Godot 侧 GDScript（`addons/` `scripts/` `autoload/` `scenes/`） | 30 | 2,171 |
| Rust `tools/gd-guard/` | 1 | 411 |
| 生成产物 `projects/` | 27 个项目目录 | — |

体量最大的 5 个文件，也是这个系统的重心所在：

| 文件 | 大小 | 职责 |
|------|------|------|
| `src/core/graph/workflow.py` | 85 KB | 工作流 + 后处理闭环 |
| `src/engine/godot/scene_to_godot.py` | 83 KB | `.tscn` 确定性序列化器 + 内嵌运行时脚本 |
| `src/api/main.py` | 57 KB | FastAPI 应用与全部路由 |
| `src/agents/code_generator/__init__.py` | 55 KB | 代码生成流水线（6 个 Phase） |
| `src/core/tools/godot_ui_tools.py` | 48 KB | UI 场景工具链 |

Python 版本约束 `>=3.11,<3.14`（`pyproject.toml:11`），全异步。

---

## 2. 技术栈全景

| 类别 | 选型 | 为什么是它 | 关键实现位置 |
|------|------|-----------|-------------|
| Agent 编排 | LangGraph `StateGraph` | 需要的是**声明式状态合并**，不是复杂拓扑 | `core/graph/workflow.py:198-228` |
| LLM 接入 | `AsyncOpenAI` 统一 6 家 provider | 6 家全部兼容 OpenAI 协议，省 6 套 SDK | `utils/llm_client.py:550-576` |
| Web | FastAPI + Uvicorn | 原生 async + SSE | `api/main.py` |
| 中间件 | **手写纯 ASGI**（不用 BaseHTTPMiddleware） | 中间件不能缓冲 body，否则 SSE 被攒批 | `api/middleware/__init__.py:1-5` |
| 游戏引擎 | Godot 4.6（headless CLI 驱动） | 唯一支持"脚本即文本 + 无 GUI 可校验"的开源 2D 引擎 | `engine/godot/__init__.py:271` |
| 安全扫描 | **Rust 手写、零第三方依赖** | 信任边界清晰；对不可信输入不 panic | `tools/gd-guard/src/main.rs` |
| 数据库 | MySQL（默认）/ SQLite / PG，SQLAlchemy | DB 只是内存任务表的镜像 | `db/models.py`、`core/concurrency.py:87` |
| 前端 | 原生 HTML/JS，无框架 | 省构建链；SSE 需手写流解析 | `digital-life-system-spatial.html:1354` |
| 图像工具 | Pillow | 只用于 playtest 空帧检测（灰度 stddev） | `engine/godot/playtest.py:81-108` |
| 可选 | Qdrant / Redis / Prometheus / Celery | 全部可选，缺失即禁用，不阻塞主流程 | `requirements.txt:54-69` |

---

## 3. 分层拆解

### 3.1 编排层：LangGraph 用得非常克制

`workflow.py:198-228` 是全部图定义，实际只 `add_node` 了 **5 个节点**：

```
requirement_analyzer → game_designer → planner → orchestrator ⇄ code_generator
```

- 4 条固定边 + **1 条条件边**（`_route_after_code_generator`，`:219-226`）：读 `state["is_complete"]`，`True` → END，否则回 `orchestrator`。
- orchestrator 的终止判据在 `_orchestrator_node`（`:368-385`）：`_get_all_ready_tasks()` 为空（即没有"依赖已全部完成"的 pending 任务）即置 `is_complete=True`。
- **没有 checkpointer**：`workflow.compile()` 裸编译，全仓无 `MemorySaver` / `SqliteSaver`。即流程不可中断续跑。
- **没有原生并行分支**：无 fan-out、无 `Send`。

**为什么这样选**：用 LangGraph 的真正收益是 `Annotated + reducer` 带来的状态合并语义——多 Agent 并发写同一个 state 字段时，框架自动按 reducer 合并，而不是互相覆盖。图拓扑本身简单到用 while 循环也能写，但状态合并写不好。这是"选它买 reducer，顺带要路由"的取舍。

**"并行场景生成"的真实实现**（这是最容易误解的一点）：

```python
# workflow.py:1656
scene_task = asyncio.create_task(self._run_scene_generation(state, event_callback))
...
async for event in self.graph.astream_events(state, version="v2", ...):  # :1670
```

场景生成跑在**图外**，与图执行同时进行。收口在 `_post_process` 的 `asyncio.wait_for(scene_task, timeout=60)`（`:1355`）。因为场景任务写的是 ainvoke 之前的同一个 `state` dict，图返回后还要**手工把 `scene_*` 字段合并回 `final_state`**（`:1732-1738`），且代码注释专门解释了为什么不能用"键是否存在"来判断（初始值里已经有 `scene_status="pending"`）。

### 3.2 状态层：reducer 是多 Agent 不打架的关键

`core/state/game_state.py` 定义 `GameDevState`，两类自定义 reducer：

```python
def merge_dicts(old, new):      # :14-20  字典合并（新值覆盖同 key）
def append_to_bus(old, new):    # :23-31  列表追加
```

挂载点：
- `code_generated: Annotated[Dict[str, str], merge_dicts]`（`:155`）—— 场景任务与代码管线会同时写它
- `error_log` / `warnings` / `fix_history` / `code_artifacts`: `Annotated[List, operator.add]`（`:163/174/198/156`）
- `message_bus: Annotated[..., append_to_bus]`（`:204`）

未标注的普通字段（`current_phase`、`is_complete`）是后写覆盖——这类字段只有一个写者，是安全的。

`core/state/bus.py:18-34` 的 `publish()` 返回 `{"message_bus": [msg]}`，交给 reducer 合并，实现 Agent 间的松耦合消息传递。

### 3.3 Agent 层：基类刻意做薄

`agents/base.py` 只抽象了很窄的一层：

| 能力 | 是否在 BaseAgent | 实际位置 |
|------|-----------------|---------|
| `execute()` 抽象方法 | ✅ `:58-69` | — |
| prompt 加载 + 全局约束拼接 | ✅ `:101-133` | 读 `config/prompts/{name}.txt`，前置拼 `global_system.txt` |
| 按角色取 provider/model | ✅ `:36-47` | 读 `llm.models[self.agent_type]` |
| 结构化日志 | ✅ `:71-99` | structlog |
| **LLM 调用 / 重试 / 熔断** | ❌ | 下沉到 `utils/llm_client.py` |
| **JSON 解析** | ❌ | 下沉到 `utils/json_extractor.py` |
| **降级 fallback** | ❌ | 各 Agent 自己实现 `_fallback_*` |

**为什么**：LLM 客户端和 JSON 提取是横切关注点，放在基类会导致每个 Agent 都要理解重试策略；放在基类外面，Agent 只关心"我要什么数据"。代价是 `_fallback_*` 逻辑在各 Agent 里重复（见 3.5）。

### 3.4 LLM 层：统一走 OpenAI 兼容协议

`config.yaml:26-44` 注册 6 家：mimo / deepseek / zhipu / kimi / sensenova / stepfun，**全部走 `AsyncOpenAI` HTTP，不用各家 SDK**。理由很直接：这 6 家都提供 OpenAI 兼容端点，一套客户端 + 一份 `base_url` 表就够了。

按角色分模型（`config.yaml:45-105`）——这是很务实的一层设计：

| 角色 | temperature | 理由 |
|------|------------|------|
| `code_generator` | 0.2 | 代码要稳 |
| `debugger` | 0.1 | 修 bug 要最保守 |
| `planner` / `game_designer` | 0.4 | 创意任务放宽 |
| `visual_reviewer` | 0.1 | 打分要可复现 |

可靠性机制（`utils/llm_client.py`）：

| 机制 | 实现 | 位置 |
|------|------|------|
| 指数退避 + 抖动 | `RetryConfig.get_delay` | `:39-51` |
| 熔断器 | `CircuitBreaker` 三态，阈值 5 / 恢复 60s / 半开 3 次 | `:54-115` |
| 客户端池 | `LLMClientPool` 单例，`AsyncOpenAI(max_retries=3, timeout=120)` | `:118-149` |
| 离线 stub | `GAMEFORGE_LLM_STUB=1` → 所有 chat 抛 `LLMStubUnavailable` | `:435-502` |

注意熔断器是**每 `LLMClient` 实例级**，不是全局——所以某个 provider 挂了不会连累其他 provider。

### 3.5 降级链：三层兜底

这是"流程不硬失败"原则的落地。以代码生成为例（`code_generator/__init__.py:282-295`）：

```
① 整机模板 → ② 组件参数化模板（godot_templates.build_artifact:468）→ ③ LLM 生成
```

注意顺序是**模板优先**，LLM 是最后的增强手段，不是唯一路径。

各 Agent 的兜底实现：

| Agent | 兜底方法 | 做法 |
|-------|---------|------|
| RequirementAnalyzer | `_fallback_spec` `:146-181` | 关键词判 genre + 硬编码默认值 |
| GameDesigner | `_fallback_gdm` `:274-421` | 关键词拼 systems/entities/code_modules + 正则抽标题 |
| Planner | `_create_sample_task_plan` `:360-401` | 返回 3 个固定任务 |
| CodeGenerator | `_fallback_generate` `:706` | 模板生成 |

**设计意图**：`GAMEFORGE_LLM_STUB=1` 让所有 LLM 调用立即失败，从而强制走完全确定性路径——这是无网络、无 API key 也能跑的冒烟测试开关。**这是一个很聪明的测试策略**：不是 mock LLM，而是让 LLM 真的不可用。

### 3.6 代码生成管线：从 6 个 Agent 收敛成 6 个 Phase

README 里的 CodeReviewer / Refactor / Debugger / TestGenerator 已经**不是独立图节点**，而是 `CodeGeneratorAgent.run_pipeline()` 内部的 Phase（`code_generator/__init__.py:962`）：

```
generate(:978) → review(:1017) → refactor(:1021) → test(:1029) → fix(:1041) → final_check(:1046)
```

代码里的明确理由（`game_state.py:60-65`、`code_generator:960`）：

> 精简为 6 个核心 Agent，其余能力（review/refactor/test/debug/final_check）由 CodeGeneratorAgent 内部 Pipeline Phase 承担

**为什么这样改**：图节点越多，状态契约越复杂、LangGraph 的往返次数越多、每轮都要过一遍 reducer 合并。把串行的、强耦合的审查/修复收进一个节点内部，减少了 state 序列化和跨节点传递的开销。

⚠️ **实测到的空转**：`fast_mode` 默认 `True` 时，`_refactor`（`:1163-1170`）直接原样返回，不做事。也就是说这个 Phase 在当前默认配置下是名义存在。真正生效的修复轮次在 `workflow.py:562` 的 `_godot_compile_loop(max_rounds=3)`。

### 3.7 引擎层：IR 抽象 + 确定性序列化

**这是整个项目技术上最正确的决策。**

LLM 只输出 `SceneIR`（`agents/scene_ir.py:70-98`）——pydantic 模型，约 10 个字段（scene_name / genre / layout / difficulty / camera / entities / theme），带白名单枚举和 validator 兜底（非法 role 自动降级为 decoration）。

再由 `scene_to_godot.py:105-751` 的 `build_scene_tscn()` **手工拼字符串**生成 `.tscn`：

```python
parts = [header, *ext_lines, *sub_resources, *nodes, *connections]  # :739-751
```

关键处理：
- ExtResource 动态编号 `_ext()`（`:169-173`），id 形如 `2_script`
- SubResource 承载碰撞体：`[sub_resource type="RectangleShape2D" id="shape_Player"]`（`:221-223`）
- role → 节点类型映射（`:151-157`）
- `_sanitize_node_name()` 正则剔除引号/斜杠/换行（`:86-93`）——防止 LLM 的字符串截断整个文件

**为什么不让 LLM 直接写 `.tscn`**：`.tscn` 格式对 ExtResource 的 id 计数、`parent` 路径、`owner`、节点重名都极其敏感，而且**错误延迟到 Godot 解析期才暴露、没有任何类型检查**。让 LLM 直出几千行必然崩溃。IR 层把"语义"和"合法语法"分离：LLM 只碰前者，后者由可测试的确定性代码保证，还能按 seed 复现。

同一份 IR 还被复用于布局规划（`plan_layout` / `FeelProfile`，`:125-147`）和美术/音频/动画管线——这是一次抽象、多处受益。

`scene_to_godot.py` 另半壁是 `RUNTIME_SCRIPTS`（`:759-1771`），约 1000 行**内嵌 GDScript 运行时模板**（player / game_flow / hud / parallax_bg + 六品类 grid_runtime）。这是"模板优先"策略的物理载体。

### 3.8 四道验收闸门（真正的护城河）

后处理段（`workflow.py:1749-1815`）的执行顺序是严格串行的：

#### 闸门 1：gd-guard 安全扫描

Rust 二进制（`tools/gd-guard/src/main.rs`，411 行，**零第三方依赖**，`Cargo.toml` 无 `[dependencies]`）。设计理由写在 `main.rs:7-14`：对不可信输入"畸形文件只产生 finding 不 panic"、信任边界清晰、不依赖 crates.io。

扫描内容（`main.rs:21-46`，24 条黑名单）：
- `OS.execute` / `create_process` / `shell_open`
- `FileAccess` / `DirAccess` / `ResourceSaver.save`
- `TCP` / `UDP` / `HTTP` / `WebSocket` / `ENet` / `Multiplayer`
- `JavaScriptBridge` / `ClassDB` 反射 / `Performance` / `Engine` 探针

外加 `.tscn` 的 `ext_resource` 逃逸检测（`:168`）、内嵌 GDScript 按不可信扫（`:212`）、`project.godot` autoload 白名单（`:264`）、原生库 `.dll/.so/.py` 检测（`:301`）。

Python 侧是**包装调用**（`engine/godot/gd_guard.py:52`）：`subprocess.run([guard, "scan", path])`，`returncode==1` → block。**缺失/超时/异常一律 fail-open**（`available=False`，不阻塞主流程）——这是明确的可用性优先取舍。

**有界反馈回路**（`workflow.py:157-193`）：block 时取 `findings[:5]` 拼成中文指令 → `code_generator.fix_code()` → 重写盘 → 复检。**只重生成一轮**，仍 block 则 `runnable=False` 并终止。注释写明这是"多智能体协作减少修改次数的核心闭环"。

#### 闸门 2：Godot headless 编译校验

不是简单跑 `--check-only`，而是逐脚本强制重解析（`engine/godot/__init__.py:271-274`）：

```python
cmd = [self.editor_path, "--headless", "--script",
       "res://addons/gameforge/syntax_check.gd", "--path", self.project_path]
```

`syntax_check.gd:32-39` 用 `ResourceLoader.load(path, "Script", CACHE_MODE_IGNORE)` 对每个脚本强制重解析，失败 `printerr("GFCHECK_FAIL: ")`。Python 侧 `_parse_headless_errors()`（`:314-342`）把 `Failed to load script` 与相邻的 `SCRIPT ERROR:` 配对成 `{file, message}`。

修复闭环 `_godot_compile_loop_headless`（`workflow.py:729-781`，**max_rounds=3**）：错误 → `error_log` → `fix_code()` → 重写盘 → 重校验。3 轮仍错则 emit `partial`。

#### 闸门 3：运行时冒烟

`--headless --quit-after 60`（`runtime_smoke.py:207-213`），用正则清单（`:26-35`）匹配错误输出，通过条件 = `returncode==0 且无匹配`（`:254`）。修复上限 `max_fix_attempts=2`。

**为什么需要**：编译通过只能说明语法对，不能说明 `_ready()` 不炸。这一个开关能抓住绝大多数空引用、类型错误。

#### 闸门 4：playtest 回放 + VLM 视觉审查

验收原则写在 `config.yaml:218` 注释里：**"编译通过 ≠ 可玩"**。

- 动作脚本是 JSON 数组，`t` 为秒，type ∈ `key|action|mouse_button|mouse_motion`（`playtest_recorder.gd:12-16`）
- `_dispatch()`（`:109-141`）用 `Input.parse_input_event()` 注入**真实 InputEvent**——不是模拟状态，是真按键
- 抓帧在 Godot 进程内 (`frame % interval == 0` → `save_png()`，`:100-103`)，固定步长保证可复现
- **空帧回退机制**（很妙的一笔）：Godot 4 的 headless `dummy` 渲染器没有渲染服务器，`get_texture()` 只能拿到纯色。`detect_blank_frames()`（`playtest.py:81-108`）用 Pillow 灰度化取 `stddev`，均值 < 2.0 判为空帧，触发**自动切窗口模式重跑**（`:281-294`）

VLM 审查（`visual_review.py`）把帧交给多模态模型（默认 zhipu/glm-4v-plus，`:21-25`），按 5 类失败模式清单打分（`_CHECKLIST`，`:42-55`）：渲染失败 / 精灵错位 / UI 不可读 / 风格混杂 / 相机越界。只允许输出 `{overall_pass, summary, issues:[{severity:high|medium|low}]}`。`severity=high` 触发修复循环（`workflow.py:946-978`）。

**这四道闸门串联起来，才是"AI 生成的游戏真的能跑"这个承诺的实现方式。**

### 3.9 API 层与流式推送

**中间件链**（`api/main.py:174-251`，注册顺序与执行顺序相反）：

```
请求 → StaticCache → APIKeyAuth → RateLimit → ConcurrencyLimit
     → RequestMetrics → InputValidation → RequestBodyLimit(2MB)
     → GZip → CORS → SecurityHeaders → 路由
```

全部**纯 ASGI 手写、不缓冲 body**（`middleware/__init__.py:1-5`）——这是 SSE 能真正流式的前提。若用 Starlette 的 `BaseHTTPMiddleware`，body 会被缓冲，SSE 就变成攒批一次性输出。

**安全兜底**（`main.py:216-220`）：无 `GAMEFORGE_API_KEYS` 且未显式设 `GAMEFORGE_ALLOW_INSECURE_LOCALHOST=true` 时直接 `RuntimeError`；未鉴权时只允许绑 `127.0.0.1/::1/localhost`。逻辑是：没有 API Key 就等于零鉴权，必须用"仅回环"来兜底。

**SSE 实现**（`main.py:543-605`）：

```python
queue: asyncio.Queue = asyncio.Queue()
async def callback(event_type, data):
    await queue.put({"event": event_type, "data": data})
workflow_task = asyncio.create_task(workflow.run_with_streaming(..., event_callback=callback))
event = await asyncio.wait_for(queue.get(), timeout=15.0)   # :584 超时发 ": ping" 心跳
```

事件源是 LangGraph 的 `astream_events(version="v2")`（`workflow.py:1670`），把 `on_chain_start/end` 翻译成 `phase_start` / `game_design` / `task_plan` / `genre` / `code_file` / `review_result`（`:1680-1724`）。

几个细节做得到位：断连时 `is_disconnected()` → cancel 掉 workflow 任务（`:580-581`）；响应头带 `X-Accel-Buffering: no` 防代理缓冲（`:603`）。

**前端不用 `EventSource`**（因为它不支持 POST body），改用 `fetch` + `getReader()` 手写解析（`digital-life-system-spatial.html:1354,1364-1382`）——这是一个很少被注意到但必须做的选择。

### 3.10 并发与沙箱

`core/concurrency.py` 的 `ConcurrencyManager` 单例：
- 双信号量：workflow=5、llm=10（`:58-60`）
- `asyncio.PriorityQueue(maxsize=100)`，入队 `(priority, time, task_id, handler)`（`:154`）
- **任务真源在内存** `active_tasks`（`:66`），DB 只是镜像（`:87` 经 `asyncio.to_thread` 写 `TaskRecord`）
- `completion_event` **先 set 再持久化**（`:246-247`），避免取消时饿死等待者

沙箱（`src/sandbox/`）生命周期：`create → modify/execute → merge|rollback → destroy`。

| 后端 | 隔离手段 | 取舍 |
|------|---------|------|
| `local`（默认） | Windows Job Object（ctypes 声明内存/进程数/CPU 限制，`process.py:152-201`）；Linux `setrlimit` | 零依赖；网络仅靠 gd-guard 拦 API |
| `docker` | `network_disabled` + mem/pids limit + 只读根（`docker_runtime.py:47-83`） | 强隔离；需 docker 与镜像 |

local 后端是 **fail-closed**：Job Object 创建或关联失败即杀进程并抛错（`process.py:268-270`）。环境变量走白名单 `sanitized_env()`（`:63-67`），剥掉 `GAMEFORGE_*` / `DB*` 凭据。

权限模型 `policy/permission.py`：6 个角色（`ROLES`，`:52-73`），如 `code_agent` 限 `scripts/ scenes/ autoload/` 且 deny `release/ export/`；`qa_agent` 只读。**未知角色 fail-closed 只读**（`:90`）。

### 3.11 两个降本设计：配方库与增量生成

**已验证配方库**（`core/recipes.py`）——结果级复用：
- `save_recipe`（`:69`）**只接受 `state["runnable"] is True`**（真机冒烟通过）且代码非空
- key = `sha256(归一化需求 | 代码摘要)`（`:101`）
- `search`（`:114`）两级匹配：需求归一化后精确相等 → 关键词交集 ≥2 且覆盖率 ≥0.6 的模糊命中（`:142`）
- 命中后在 `workflow.py:1438` 的 `_run_recipe` 里**跳过 game_designer/planner/codegen 整条 LLM 流水线**，只跑后处理和构建

这是最有效的省钱手段：同类需求第二次生成几乎零 LLM 成本。

**增量生成**（`core/incremental.py`）——过程级裁剪：
- `DependencyGraph.analyze`（`:26`）正则抽 `.gd` 的 `load/preload` 引用和 `.tscn` 的 `res://` 引用建依赖图
- `compute_impact`（`:97`）BFS 求传递闭包，只重生成受影响文件

注意：`incremental_enabled` 默认 **False**（`workflow.py:63`），配方库默认 **True**（`:60`）。

---

## 4. 数据契约链

```
自然语言需求
   ↓ RequirementAnalyzer
Game Spec DSL    {game:{genre,camera,difficulty}, player:{hp,actions}, enemy:{...}, mechanics, assets, scenes}
   ↓ GameDesigner
GDM              {game_title, genre, engine, camera_mode, core_loop, player_actions,
                  win_conditions, fail_conditions, main_systems, entities, scenes,
                  code_modules, assets_needed, input_map, tags_layers, physics_settings}
   ↓ Planner
TaskPlan         {id, name, description, type, status, priority, dependencies, assigned_agent,
                  output_files, target_game_objects, required_components, acceptance_criteria}
                 + asset_plan {style, assets:[{role, asset_id, fallback_asset_id}], missing_assets}
   ↓ CodeGenerator / SceneGenerator
代码产物          code_generated: {相对路径 → 内容}（reducer 合并）
                 code_artifacts: GeneratedFileMetadata[]
```

条目的形态归一化统一收口在 `core/gdm.py:24-59` 的 `as_item` / `as_item_list`——这是防止 LLM 时而返回字符串、时而返回对象的防御层。

**品类匹配打分**（`agents/genre_specs.py:666-680`）算法很简单但有效：

```python
score = sum(1 for kw in spec.keywords if kw.lower() in text)
if spec.representative.split(" ")[0].lower() in text:
    score += 3          # 代表作名出现 = 强信号
```

命中关键词每个 +1，代表作名 +3，取最高分；`best_score == 0` 时走 `genre_fusion.roll_concept` 组合新概念（`planner:119`）。难度由 `infer_difficulty` 关键词推断，`simplify_scope` 决定 easy 走直接 MVP / hard 分期实现。

---

## 5. 发现的问题与偏差

以下是对照源码后发现的、文档与实现不一致或值得关注的点：

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| 1 | README 称"6 个核心图节点"，实际 `add_node` 只有 **5 个**（`scene_generator` 不在图内） | `workflow.py:203-207` | 文档误导 |
| 2 | `config.yaml` 仍保留 `code_reviewer` / `test_generator` / `debugger` / `refactor` 的模型条目，但对应 Agent 目录已不存在 | `config.yaml:56-75` | 历史残留配置 |
| 3 | `fast_mode` 默认 True 时 `_refactor` Phase **空转**（原样返回） | `code_generator:1163-1170` | 名义存在的 Phase |
| 4 | 评测体系的 `code_quality_score` / `naming_convention_score` **只对 `.cs` 生效**，`.gd` 不参与计算 | `eval/metrics/__init__.py:302,311` | Godot 项目这两项指标无效 |
| 5 | MCP 工具链（image/engine/asset/knowledge/test）**已实现但未接入主链路**，全仓仅 `examples/` 与 `tests/` 引用 `MCPClientManager` | `mcp/client/manager.py:232` | 当前实际只服务于外部 IDE 集成 |
| 6 | `playtest.enabled` / `sandbox.enabled` / VLM 审查**默认全部关闭** | `config.yaml:192,220` | 四道闸门中实际只有前两道默认生效 |
| 7 | `engine_server.py` 文件首字节带 UTF-8 BOM | `mcp/servers/engine_server.py` | 导入需 `encoding="utf-8-sig"` |
| 8 | 图无 checkpointer，流程不可中断续跑 | `workflow.py:228` | 长任务中断即丢失 |

其中 **#4** 和 **#6** 最值得优先处理：#4 是"评测显示 100 分但其实没测"，#6 意味着"编译通过 ≠ 可玩"这个最核心的承诺在默认配置下并未被验证。

---

## 6. 总结：这个项目的技术性格

1. **不信任 LLM 的输出格式** → 出 IR、出 JSON、出受限枚举，语法一律由代码拼
2. **不信任 LLM 的可用性** → 三层降级链，模板优先于模型，stub 模式强制离线
3. **不信任"编译通过"** → 四道闸门，最终用 VLM 看画面
4. **不信任不可信输入** → Rust 零依赖扫描器 + 沙箱白名单环境变量
5. **不浪费重复的 LLM 调用** → 配方库结果级复用 + 依赖图过程级裁剪

本质上这是一个**用确定性工程兜住概率性组件**的系统。最值得借鉴的设计是 IR 分层（把"语义"和"合法语法"解耦）和"模板优先、LLM 增强"的调用顺序——这两条决定了系统是"能跑"还是"看起来能跑"。
