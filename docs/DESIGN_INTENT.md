# GameForge 设计意图档案

> **这份文档解决的问题**：项目里没有任何地方记录"当初为什么这么设计"。
> 代码能说明"是什么"，`CLAUDE.md`/`README.md` 说明"怎么用"，但"为什么不那样、为什么改成这样"只散落在 76 个 git 提交信息、
> 几份阶段性设计文档和代码注释里。时间一长就会像现在这样——记得功能，忘了初衷。
>
> 本文档从 **git 提交史 + 项目自带设计文档 + 代码注释**三方交叉还原，每条决策都标注依据。
> 生成时间：2026-09-16

---

## 1. 一句话定位（这是所有决策的前提）

> **Godot GDScript 单引擎 + 多 Agent 流水线 + Web UI。**
> 只服务两条诉求：**① 稳定生成真实可运行的游戏；② UI 接口稳定接后端。**

这句话出自 `docs/gameforge-optimization-plan.md:3-4`，它是判断"某个想法该不该做"的唯一标准。
**凡是让这两条变得更脆弱的，哪怕是"功能升级"，也砍。**

---

## 2. 演进史：三个阶段（对照 git 提交）

仓库共 **76 个提交**，时间跨度 2026-05-15 ~ 2026-09-06。按月分布：5月17个、6月10个、7月1个、8月15个、9月33个（9月是爆发期）。

### 阶段一：Unity/Unreal 通用生成器（05-15 ~ 06-06）

| 时间 | 提交 | 关键产物 |
|------|------|---------|
| 05-15 | Initial commit | 项目骨架、核心 Agent、工作流 |
| 05-16 | v1.1 → v1.2 | 核心 bug 修复；**所有 Agent 接入 LLM，Unity 编译器直连** |
| 05-17 | v1.3 | Mimo 模型迁移 + 高并发架构 + 安全防护体系 |
| 05-18 | v1.4 | 五大局限修复 + PostgreSQL |
| 05-19 | v1.5 | **SSE 流式生成** + 聊天式 UI + 性能优化 |
| 05-22 | v2.0 | **Game Design Model (GDM)** + 完整离线产物 |
| 05-27 | v2.1 | **场景 IR 中间层** + Unity 项目模板 + 前端安全重构 |
| 06-02 | — | **多 Provider LLM 架构（按 Agent 分配不同模型）** + **LangGraph 图驱动** + Redis/Qdrant/Prometheus |
| 06-04 | — | **修复工作流无限循环** + 测试 Mock 完善 |
| 06-06 | — | Unity 一键 Demo + 缓存优化 + Canvas 预览 |

**这一阶段留下的四个抽象，全部活到了今天**，这是它们跨引擎成立的证据：

| 抽象 | 引入时间 | 今天的位置 | 为什么能活下来 |
|------|---------|-----------|--------------|
| GDM（Game Design Model） | 05-22 v2.0 | `state["game_design_model"]` | 策划语义与引擎无关 |
| Scene IR 中间层 | 05-27 v2.1 | `agents/scene_ir.py` | 场景语义与引擎无关 |
| SSE 流式生成 | 05-19 v1.5 | `api/main.py:543` | 交互需求与引擎无关 |
| 多 Provider 按角色分模型 | 06-02 | `config.yaml:45-105` | 成本/稳定性需求与引擎无关 |

### 阶段二：收敛为纯 Godot（07-05 ~ 08-17）

| 时间 | 提交 | 含义 |
|------|------|------|
| **07-05** | **`refactor: 重构为 Godot 工具链 — 完整替换 Unity/Unreal`** | 全文最重要的转折点 |
| 08-10 | `fix: port HTTP response and TSCN order fixes to new Godot structure` | 把旧成果移植到新结构 |
| 08-15 | `refactor: 收敛为纯 Godot 工具链 + 多智能体提示词第一版改良` | 第二次强调"收敛" |
| 08-17 | `feat: 完善游戏场景生成与质量评估 + 重写README` | 双引擎时代彻底结束 |

**为什么从 Unity 转向 Godot**：Unity 需要安装庞大的编辑器、C# 编译链重、场景是二进制 `.unity` 资产；而 **Godot 是单个 exe + `--headless` 无头模式 + 场景是纯文本 `.tscn`**。

对"AI 生成 + 自动验证"这个场景，后者的三个特性是决定性的：
1. 单文件可执行 → 容易版本钉死、可复现（→ 后来催生 `tools/install_godot.py`）
2. headless 可跑 → 能自动编译校验、自动跑测试（→ 催生整个验收链）
3. 纯文本场景 → 可用确定性代码序列化（→ 坐实了 Scene IR 的价值）

> **这段历史解释了第一轮分析里发现的"配置残留"**：`config.yaml:56-75` 至今还留着 `code_reviewer`/`test_generator`/`debugger`/`refactor` 的模型配置——那是 6 月之前双引擎时代的配置，不是 bug，是**没人清理的历史层**。

### 阶段三：从"能生成"到"能跑"（08-20 ~ 09-06）

| 时间 | 提交 | 解决的问题 |
|------|------|-----------|
| 08-20 | 完整闭环配置 | 启用 Redis/Qdrant/多智能体新功能 |
| 08-23 | **实时预览 Godot 真实画面渲染** + MySQL 分项注入 + 隐私审计 | 前端看到的不能再是假图（详见 §3.15） |
| 08-24 | **P0 组件参数化模板库 + P1 已验证配方语义复用** | 命中即填参跳过 LLM；配方复用"逼近 2s" |
| 08-30 | **三大升级：上线闭环 + 稳定门禁 + 美术全自动**（附 20 基款组合概念引擎） | 游戏性/稳定性/美术三条线同时补 |
| 08-31 | **gd-guard 静态安全闸门**（Rust 黑名单扫描器 + 双重接线） | 生成的脚本可能干危险的事 |
| 09-01 | **沙箱平台 Phase 1**（角色权限/任务工作区/快照回滚/Job Object 进程笼） | 不可信产物需要隔离执行 |
| **09-02** | **Agent 精简 Phase 1** | 删掉 6 个独立 Agent（详见 §3.4） |
| 09-06 | **游戏验证闭环与工程契约强化**：playtest 输入回放 / VLM 视觉审查 / 产物级评测 / 路径单一事实源 / LLM 离线 stub | "编译通过 ≠ 可玩"（详见 §3.9） |

> 8-24 那条提交信息本身就是设计意图的完整记录，值得原样保留：
> 「P0 组件参数化模板库 + P1 已验证配方语义复用 - 12类组件模板命中即填参跳过LLM生成；RecipeStore真机冒烟通过后沉淀配方，同/近需求整体复用跳过LLM主流水线逼近2s；workflow接线快速路径与bake闭环，recipes.enabled开关默认开自适应零副作用」

---

## 3. 关键设计决策及初衷（逐条可查）

### 3.1 为什么用 GDM + Scene IR 两层中间表示

**当初的问题**：让 LLM 直接输出引擎文件（`.unity`/`.tscn`）会全面崩溃。

**决策**：在"自然语言"和"引擎文件"之间插两层受约束的结构化表示——

```
自然语言 → Game Spec DSL → GDM（策划语义）→ Scene IR（场景语义）→ .tscn（引擎语法）
                             ↑ 引擎无关              ↑ 引擎无关           ↑ 引擎相关
```

**为什么有效**：LLM 只碰左边两层（字段少、有枚举、可校验），右边那层的"语法正确性"由确定性代码保证。这也解释了两个文件为什么这么大：`scene_to_godot.py` 83KB、`scene_ir.py` 只有 7KB——**复杂度被有意地压在了确定性一侧**。

**依据**：`agents/scene_ir.py:70-98`（pydantic 白名单 + validator 兜底）、`engine/godot/scene_to_godot.py:105-751`。

### 3.2 为什么选 LangGraph（买的是 reducer，不是拓扑）

**当初的问题**：手写 while 循环调度多 Agent 时，多个节点并发写同一份 state 会互相覆盖。

**决策**：用 LangGraph 的 `Annotated[type, reducer]` 让框架承担状态合并。

**关键认识**：这张图的拓扑简单到"用 while 也能写"（5 节点 + 1 条条件边，无原生并行、无 checkpointer）。**选它买的是 `merge_dicts` / `operator.add` 这套合并语义，路由是顺带的。** 所以看到"图很简单但依赖很重"不要奇怪，这是有意的取舍。

**依据**：`workflow.py:198-228`、`state/game_state.py:14-31`。

### 3.3 为什么"并行场景生成"跑在图外

**决策**：`asyncio.create_task` 在 `graph.astream_events` 之外并行跑场景生成，`_post_process` 里 `wait_for(timeout=60)` 收口。

**代价（已知且接受）**：图返回后必须手工把 `scene_*` 字段合并回 `final_state`（`workflow.py:1732-1738`），且不能用"键是否存在"判断——因为初始 state 里已经有 `scene_status="pending"` 这个默认值。代码注释专门记录了这个坑。

**为什么不改成图内并行**：那样需要 `Send` API 或 fan-out，会让状态契约复杂化，与 §3.4"减少节点"的方向冲突。

### 3.4 为什么删掉 6 个独立 Agent（09-02 "Agent 精简 Phase 1"）

**这是最容易被误解的一次改动**——从代码看像是"功能缩水"，实际是有明确判断的。

`docs/ROADMAP.md:18-20` 写明了核心判断：

> 下一步核心问题不是：
> - ❌ "我有几个 Agent"
> - ✅ "Agent 是否能持续积累经验，并越来越少修改"

**决策**：把 CodeReviewer / Refactor / TestGenerator / Debugger / MainReviewer / Reflector 六个独立节点，合并为 `CodeGeneratorAgent.run_pipeline()` 内部的 Phase。

**删除清单**（`L vs main` diff 实证）：
```
src/agents/code_reviewer/__init__.py   | 159 -
src/agents/debugger/__init__.py        | 347 --
src/agents/refactor/__init__.py        | 261 --
src/agents/test_generator/__init__.py  | 384 ---
src/agents/main_reviewer.py            | 149 -
src/agents/reflector/__init__.py       | 122 -
src/core/dialogue/                     | 已删除（review_refactor_negotiation.py + session.py）
```

**为什么**：图节点越多 → 状态契约越复杂 → 每轮都要过一遍 reducer 合并 → 往返开销越大，而**收益并没有体现在"更少的修改次数"上**。收敛成内部 Phase 后，审查/修复在同一节点内闭环，不需要跨节点传递。

**同时删掉的**还有一个很有意思的东西：`src/core/dialogue/`——曾有一个"审查-重构协商"的多轮对话机制（`review_refactor_negotiation.py`）。删掉它意味着放弃了"让两个 Agent 辩论"的路线，转向"确定性 Phase"。**这个决定值得记住：项目选择了可预测性，而不是涌现行为。**

**⚠️ 但留下了未完成的部分**：`fast_mode=True`（默认）时 `_refactor` Phase 直接原样返回，等于空转（`code_generator:1163-1170`）。这是精简过程中"先接线、后填充"的遗留，属于**还没做完，不是设计如此**。

### 3.5 为什么"模板优先于 LLM"

**决策**：代码生成顺序是 ① 整机模板 → ② 组件参数化模板 → ③ LLM 生成（`code_generator:282-295`）。

**反直觉之处**：一般系统把 LLM 当主力、模板当兜底；这里是反过来。

**当初的动机**（08-24 提交）："12 类组件模板命中即填参跳过 LLM 生成"——**模板是优化路径，不是降级路径**。快、稳、免费，而且产物可控。

**推论**：LLM 的实际职责被压缩到"模板覆盖不到的部分"。这也解释了 `scene_to_godot.py:759-1771` 里为什么要内嵌约 1000 行 GDScript 运行时模板。

### 3.6 为什么要 Recipe 配方库（08-24 P1）

**当初的问题**：同类需求反复生成，每次都要重新烧一遍 LLM。

**决策**：`RecipeStore` 只在**真机冒烟通过后**沉淀配方，key = `sha256(归一化需求 | 代码摘要)`，命中即跳过整条 LLM 流水线。

**两个关键设计**：
1. **只存 `runnable=True` 的产物**（`recipes.py:69`）——没跑通的也存，就是在传播错误。
2. **模糊命中门槛**：关键词交集 ≥2 且覆盖率 ≥0.6（`:142`）——太松会复用错东西。

**目标值**：`ROADMAP.md:235` 配方复用率 **~20% → >40%**。

### 3.7 为什么 gd-guard 用 Rust 写、且只重试一轮

**当初的问题**：Agent 生成的 GDScript 可能调用 `OS.execute` / `FileAccess` / 网络 API，在用户机器上执行危险操作。

**Rust 的理由**（写在 `main.rs:7-14`，是作者自己的记录）：
- 零第三方依赖（`Cargo.toml` 无 `[dependencies]`）→ 不依赖 crates.io、构建快、供应链干净
- 对不可信输入"畸形文件只产生 finding 不 panic" → 扫描器本身不能成为攻击面
- 信任边界清晰

**"只重试一轮"的理由**（`workflow.py:157-193`）：block → 把 findings 喂回 `fix_code` → 重写盘 → 复检；**仍 block 就终止**。目的是避免"AI 反复改同一个安全问题"的死循环，这直接呼应 06-04 那次"修复工作流无限循环"的教训。

**取舍明确**：闸门自身缺失/超时/异常一律 **fail-open**（不阻塞主流程）。可用性优先于绝对安全。

### 3.8 为什么要沙箱（09-01 Phase 1）

**决策**：`create → modify/execute → merge|rollback → destroy` 生命周期 + 角色权限 + 文件级快照。

**为什么**：这是"AI 生成的东西在本地跑"这个前提的必要配套。三级隔离可选：
- `local`：Windows Job Object / Linux setrlimit（**fail-closed**，限制建不起来就杀进程）
- `docker`：网络禁用 + 内存/pids 限制 + 只读根

**注意**：默认 `enabled: false`。**这是一个尚未启用的底座**，不是正在跑的机制。

### 3.9 为什么要 playtest + VLM（09-06，"编译通过 ≠ 可玩"）

**这是整条验收链的落脚点，也是最贵的部分。**

**当初的问题**：`optimization-plan.md:34` 的诊断——

> 当前验证终点是 `check_scripts`（headless 语法检查），**只证明"语法对"，不证明"能加载/能跑"**。

**决策（P0-2 运行时冒烟）**：把"编译通过"升级为"跑得起来"。提交信息里的原则是 **"编译通过 ≠ 可玩"**。

**四道闸门的递进关系**：
```
gd-guard         → 脚本安全吗？      （静态，不执行）
headless 编译     → 语法对吗？        （解析，不运行）
运行时冒烟        → 能加载能启动吗？  （真跑 60 帧）
playtest + VLM    → 玩起来对吗？      （注入真实输入 + 看画面）
```

**"proof over claims" 的来源**：`optimization-plan.md:74` 明确写这是"godogen 'proof over claims' 的 Godot 落地，但**不引入任何新引擎/素材依赖**"。也就是说：借理念，不借架构。

### 3.10 为什么产物放 `.gameforge/` 点号目录

**当初的问题**：流水线的中间产物（录像帧、报告、评测）如果放在游戏项目里，会污染游戏工程、被 Godot 导入器扫描。

**决策**：全部收在 `projects/<project_id>/.gameforge/<run_id>/`，**利用 Godot 忽略点号目录的特性**。

**编址维度**：`(project_id, run_id, task_kind, task_id)`。

**依据**：`core/paths.py:8-28`。

### 3.11 为什么 `paths.py` 是唯一路径来源

**决策**：所有产物路径只在一个模块构造，并给出一条**可机械验证的约束**：

```bash
grep -rn '"projects"' src/ --include="*.py" | grep -v paths.py   # 必须为空
```

**为什么**：路径散落会导致"写盘位置和读取位置不一致"——这正是 `optimization-plan.md` 诊断出的第 3 号问题（前端读 `Assets/Scenes/...` ≠ 后端写 `scenes/...`，导致试玩按钮永不出现，Unity 遗留）。

**这是一种很好的架构治理方式：把架构约定写成一条能跑的 grep。**

### 3.12 为什么统一 `OperationResult` 契约（09-06）

**当初的问题**：运行时冒烟、playtest、视觉审查各自发明 dict 形态，调用方和测试都要猜。

**决策**：`core/result.py` 定义信封 `gameforge.operation_result.v1`。

**最值得记住的一条约定**：

> `ok` 的**成功判定由模块用原生产物得出，退出码 0 不算依据**。

**为什么这条重要**：进程退出码 0 ≠ 游戏能玩。这与 §3.9 是同一个立场——**不接受代理指标，只接受真实证据**。

其他约定：`skipped`（环境/配置未执行）与 `ok=False`（失败）严格区分；`ok=False` 时 `errors` 必须非空（**失败必须可归因**）。

### 3.13 为什么 API 中间件手写纯 ASGI

**当初的问题**：Starlette 的 `BaseHTTPMiddleware` 会缓冲 body，SSE 会退化成"攒批一次性输出"。

**决策**：整条中间件链（安全头/CORS/GZip/请求体限制/输入校验/指标/并发/限流）全部手写纯 ASGI，**不缓冲 body**。

**依据**：`api/middleware/__init__.py:1-5` 的注释直接写明。

### 3.14 为什么前端不用 `EventSource`

**原因**：`EventSource` 不支持 POST body，而生成请求需要提交需求文本。

**决策**：`fetch` + `getReader()` 手写流解析（`digital-life-system-spatial.html:1354,1364-1382`）。

### 3.15 为什么"headless 空帧要回退窗口模式"（同一个坑踩了两次）

**这个坑值得单独记，因为它解释了两处看起来奇怪的代码。**

**坑的本质**：Godot 4 的 `--headless` 默认启用 `dummy` 渲染器，**没有渲染服务器**，`Viewport.get_texture().get_image()` 拿到的是空/纯色。

**第一次踩到（实时预览，08-21 设计文档）**：`realtime-preview-design.md:23-26` 记录——

> 当前靠 Godot 脚本 `_build_placeholder_image()` 程序化生成占位 PNG（顶部青色条 + 底部紫色条 + 中心白色滑条）维持链路连通。
> **前端表现**：每 1200ms 拉一帧，TAG 从 `SIM` 切到 `LIVE`，但画面内容是静态占位图，不是游戏。

于是有了 08-23 的"实时预览真实画面渲染"。2.0 方案（`realtime-preview-design.md:28-38`）的目标里有一条很关键：**"不抢占用户桌面"**——所以 Godot 窗口要 `--position 20000,20000` 放到屏幕外，且**绝不能最小化**（最小化后 PrintWindow 抓不到内容），最后用 `PrintWindow + PW_RENDERFULLCONTENT` 抓 OpenGL 内容。

**第二次踩到（playtest，09-06）**：同样的 headless 空帧问题，解决方案一致——`detect_blank_frames()` 用 Pillow 灰度 stddev < 2.0 判空帧，自动切窗口模式重跑一次。

> **记忆点**：以后任何"在 Godot 里抓真实画面"的需求，先确认渲染器不是 dummy，并预留窗口模式回退。这个项目里所有"看起来绕"的截图代码，根源都在这里。

### 3.16 为什么 Redis / Qdrant / Prometheus 都是可选的

**决策**：全部标为可选依赖，缺失即禁用功能，**不阻塞主流程**。

**为什么**：`optimization-plan.md:52` 的健康基线记录——"降级链（LLM→模板→硬编码、桩脚本兜底）已完善，故'流程不中断'不是短板"。项目把"任何外部依赖都可能不存在"当作默认前提。

**极端形态**：`GAMEFORGE_LLM_STUB=1` 让所有 LLM 调用立即抛异常，强制走确定性路径——**不是 mock LLM，而是让 LLM 真的不可用**。这是很聪明的测试策略。

---

## 4. 明确不做的事（防跑偏护栏）

`optimization-plan.md:109-113` 有一份"明确不做"清单，**这比"要做什么"更能定义项目边界**：

| 不做 | 理由 |
|------|------|
| ❌ 多引擎支持（Bevy/Babylon） | 与"Godot GDScript 单引擎"定位冲突 |
| ❌ godogen 的 3D 素材管线（Tripo3D/骨骼/视频精灵） | 超出"Godot GDScript 生成"定位 |
| ❌ 主 UI 实时 preview 轮询 | **因为脆弱**——依赖 `mss`/`PrintWindow` 截屏屏幕外窗口，踩坑多，接进主 UI 反而降低稳定性 |

`optimization-plan.md:20-35` 还记录了一次**批判性筛选**，其中两条"砍掉"很典型：
- 砍掉"修 `review_game_design` 的 async 竞态"——因为核实后发现那是个**纯确定性同步函数**，不存在竞态。**先验证问题是否真实存在，再决定修不修。**
- 降级"LLM 探活"——401 静默降级确实是隐患，但复杂探活过度设计，降为"启动一次探活 + 401 显式报错"。

---

## 5. 核心判断与阶段规划（`docs/ROADMAP.md`）

> **GameForge 已度过 "Demo 阶段"，进入 "自进化工厂" 阶段。**
> 下一步核心问题不是"我有几个 Agent"，而是"**Agent 是否能持续积累经验，并越来越少修改**"。

### Phase 1：减少无效调用（收益最大、难度最低）

| 优化项 | 目标 | **当前实现状态** |
|--------|------|-----------------|
| 1.1 Requirement Analyzer | 新增前置节点，标准化需求输入 | ✅ 已实现（`agents/requirement_analyzer/`，L 分支新增） |
| 1.2 Game DSL | GDM → YAML DSL，减少 Agent 间自然语言传递 | ✅ 已实现（`core/dsl/__init__.py`） |
| 1.3 Incremental Generation | 增量修改，避免全量重生成 | ⚠️ 已实现但**默认关闭**（`workflow.py:63`） |
| 1.4 Memory Enhancement | 记忆结构化、跨项目、向量检索 | ⚠️ 部分（`core/memory/` 存在，向量化未接主链路） |

### Phase 2 / 3（未开始）

- Phase 2：Evaluation Agent（技术+游戏性+美术三维评估）、World Agent（场景→世界构建）、Dynamic Task Graph（固定 DAG → 动态依赖图）
- Phase 3：Recipe Evolution（配方自动进化评分）、Bug Knowledge Base、Auto Repair Memory

### 目标指标（Phase 1 完成后）

| 指标 | 当前 | 目标 |
|------|------|------|
| Agent 调用次数 | 5-8 次/需求 | 3-5 次/需求 |
| 生成成功率 | ~70% | >90% |
| 返工次数 | 2-3 次 | 0-1 次 |
| 配方复用率 | ~20% | >40% |

---

## 6. 当前仓库状态（必须知道的事实）

### 分支状态

| 项 | 值 |
|----|-----|
| 当前分支 | **`L`**（不是 main） |
| HEAD | `d1cf84d` 2026-09-06 17:17 |
| `main..L` 差异 | **643 files changed, 47922 insertions(+), 10188 deletions(-)** |
| 存在分支 | `L`（当前）、`main`、`master`、`stash-backup` |
| 远程 | `origin` → `github.com:samllsix/gameforge.git` |

> ⚠️ **`L` 分支领先 `main` 近 4.8 万行**——整个"从 Unity 到 Godot"之后的演进都在 L 上，main 是历史快照。

### 未提交的工作（进行中的事）

**新增未跟踪文件**（代表下一步方向）：

| 文件 | 推测意图 |
|------|---------|
| `src/engine/godot/layout_planner.py` + `tests/unit/test_layout_planner.py` | 布局规划独立成模块（原在 `scene_to_godot.py` 内） |
| `src/engine/godot/character_anim.py` + `tests/unit/test_character_anim.py` | 角色动画生成 |
| `src/core/result.py` + `tests/unit/test_result_contract.py` | 操作结果契约（§3.12） |
| `tools/install_godot.py` + `tests/unit/test_install_godot.py` | 引擎版本钉死安装器 |
| `tests/unit/test_build_workbench.py` | 构建工作台测试 |
| `scenes/PlatformerLevel1.tscn`、`projects/smoke_layout_test/`、`projects/space_demo/` | 验证产物 |

**已修改未提交**：`workflow` 相关（`api/main.py`、`playtest.py`、`runtime_smoke.py`、`scene_to_godot.py`、`supervisor.py`、`visual_review.py`）、前端 `digital-life-system-spatial.html`、`config/config.yaml`、`requirements.txt` 及对应测试。

---

## 7. "看起来矛盾的地方"对照表

把第一轮技术分析中发现的偏差，与本文档还原的意图对照，区分**"有意为之"**与**"真的忘了清理"**：

| 现象 | 性质 | 说明 |
|------|------|------|
| `config.yaml` 仍有已删除 Agent 的模型配置 | 🟡 **历史层残留** | 双引擎时代 + Agent 精简后的遗留，可清理 |
| README 说"6 个图节点"实际 5 个 | 🟡 **文档滞后** | 精简后未同步文档 |
| `_refactor` Phase 在 `fast_mode` 下空转 | 🔴 **未完成** | 精简时"先接线后填充"，不是设计如此 |
| `eval/metrics` 的质量/命名指标只对 `.cs` 生效 | 🔴 **Unity 遗留 bug** | Godot 项目这两项指标实际无效，静默通过 |
| `playtest` / `sandbox` 默认关闭 | 🟢 **有意为之** | 能力已建好但尚未启用，属"底座先行" |
| MCP 工具链未接入主链路 | 🟢 **有意为之** | 定位是给外部 IDE 用的能力出口，非内部组件 |
| 实时预览"看起来绕"（屏幕外窗口 + PrintWindow） | 🟢 **有意为之** | 见 §3.15，为"不抢占用户桌面"+ 绕开 dummy 渲染器 |
| 场景生成不在 LangGraph 图内 | 🟢 **有意为之** | 见 §3.3，换取图拓扑简单 |
| 中间件全手写、不用 Starlette 现成的 | 🟢 **有意为之** | 见 §3.13，为了 SSE 不被攒批 |
| 配置里 Redis/Qdrant 存在但可缺失 | 🟢 **有意为之** | 见 §3.16，外部依赖默认可不存在 |

---

## 8. 如果只记三件事

1. **定位**：Godot GDScript 单引擎。不碰多引擎、不碰 3D 素材管线。判断新想法该不该做，问"它让'稳定生成可运行游戏'和'UI 稳定接后端'变强还是变脆"。
2. **路线**：项目已过 Demo 期，进入"自进化工厂"期。核心指标不是 Agent 数量，而是**修改次数下降**和**配方复用率上升**。所有抽象（模板库、配方库、记忆）都在服务这个目标。
3. **立场**：**不接受代理指标，只接受真实证据**。语法通过不算能跑，退出码 0 不算成功，必须真启动、真回放、真看画面。这条立场贯穿四道验收闸门、`OperationResult` 契约和模板优先策略。

---

## 附：本文档依据来源

| 来源 | 用途 |
|------|------|
| `git log`（76 提交，2026-05-15 ~ 09-06） | 演进时间线、转折点、删除清单 |
| `git diff main..L --stat` | 确认 Agent 精简范围、前端替换、新增模块 |
| `docs/ROADMAP.md` | 核心判断、三阶段规划、目标指标 |
| `docs/gameforge-optimization-plan.md` | 定位表述、"明确不做"清单、批判性筛选过程、P0/P1 诊断 |
| `docs/realtime-preview-design.md` | headless dummy 渲染器踩坑记录、实时预览目标 |
| `docs/AGENT_SERVICE_PLAN.md` | **反面参照**——精简前的 Agent 清单，已过时 |
| `src/core/result.py` | 操作结果契约的设计缘由（含"退出码 0 不算依据"） |
| `src/core/paths.py` | 路径单一事实源 + `.gameforge` 编址约定 |
| `src/core/dsl/__init__.py` | Game DSL 的定位 |
| `CLAUDE.md` / `README.md` | 对外表述（注：部分内容滞后于实现） |
