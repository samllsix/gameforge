# GameForge 优化方案（定稿 · 经批判性筛选）

> 目标：在**不偏离核心**（Godot GDScript 单引擎 + 多 Agent 流水线 + Web UI）的前提下，
> 达成两条诉求：**① 稳定生成真实可运行的游戏；② UI 接口稳定接后端。**

---

## 0. 结论先行

上一轮方案有 2 项**过度设计需砍掉**、1 项**降级**，并**漏掉了一个真正命中的核心 bug**。
修正后的优先级：

- **P0（核心可运行性）**：① 修 scene_builder 的 2D/3D 资源混用 → ② 加运行时冒烟测试（load+run 场景）。
- **P1（UI 稳定接后端）**：③ 前端契约修复 → ④ 契约冒烟测试。
- **P2（可选加固）**：⑤ LLM 启动探活（轻量）。
- **明确不做**：多引擎、3D 素材管线、主 UI 实时 preview 轮询。

---

## 1. 批判性筛选（哪些方案真的适合？）

### ❌ 砍掉：主 UI 接入实时 preview 轮询
- 理由：preview 依赖 `mss`/`PrintWindow` 截屏**屏幕外(20000,20000)窗口**，本身脆弱（项目记忆里记录了大量踩坑：窗口位置失效、黑屏检测、PrintWindow 失败回退）。把它接进主 UI 轮询，**反而降低"稳定"**。
- 且它对"生成可运行游戏"是锦上添花，非雪中送炭。
- 处置：**不做**。`/api/v1/preview/frame` 保留为独立调试能力。

### ❌ 砍掉：修 `review_game_design` 的 async 竞态
- 理由：已核实 `main_reviewer.review_game_design` 是**纯确定性同步函数**（`main_reviewer.py:118`，只做字段存在性检查，不调 LLM）。阻塞事件循环时间可忽略，不存在竞态问题。

### ⚠️ 降级：LLM 探活
- 理由：401 静默降级确实是隐患，但做成复杂探活过度。降为**启动时一次探活 + 401 时显式报错**即可。

### ✅ 强化并提升：运行时冒烟测试（上一轮列为阶段2，现升为 P0）
- 理由：这是**唯一能回答"产物到底能不能跑"的机制**。当前验证终点是 `check_scripts`（headless 语法检查），只证明"语法对"，不证明"能加载/能跑"。而 scene_builder 产出的 `.tscn` 有 2D/3D 混用等隐患，只有真正 load+run 才能暴露。

### ✅ 新增：修 scene_builder 2D/3D 资源混用（上一轮漏掉，现列为 P0 之首）
- 证据：`scenes/` 下 5 个 `.tscn` 全部同时含 `MeshInstance2D` + `StandardMaterial3D`。

---

## 2. 诊断结论（代码级事实）

| # | 事实 | 位置 | 影响 |
|---|------|------|------|
| 1 | 2D 场景用 3D 材质/网格着色 | `scene_builder.py:257-264, 395-407`（`_build_background`/`_build_visual_node` 用 `StandardMaterial3D`+`QuadMesh`+`MeshInstance2D`） | 场景加载/渲染异常，是"可运行"首要风险 |
| 2 | 验证终点=语法编译，不验证运行 | `workflow.py` `_godot_compile_loop_headless` / `engine/godot/__init__.py:check_scripts` | 运行时错误不拦截 |
| 3 | 前端"试玩"读 `Assets/Scenes/...`（Unity 遗留）≠ 后端 `scenes/...` | `app.js:839` vs `workflow.py:986` | 试玩按钮永不出现 |
| 4 | 前端分类/图标仍 `.cs`/`Tests.cs`/`Editor/` | `app.js:categorizeFiles/previewFile/updateFileTree` | Godot 文件归错类、图标错 |
| 5 | 前端处理 `game_design`/`task_plan` 事件，后端 streaming 从不发 | `app.js:622/647` vs `workflow.py` | 设计/规划时间线不亮 |
| 6 | 无 API/契约测试（旧 `test_api.py` 已不在） | `tests/unit/` | 契约漂移无人拦截 |

**健康基线**：当前单测 134/135 绿（1 失败为测试环境变量污染）。降级链（LLM→模板→硬编码、桩脚本兜底）已完善，故"流程不中断"不是短板。

---

## 3. 完整方案

### P0-1 修 scene_builder 2D/3D 资源混用（核心可运行性）

- **做什么**：2D 场景的视觉/背景着色，从 `MeshInstance2D + StandardMaterial3D + QuadMesh` 改为 2D 原生方案：
  - 背景 → `ColorRect`（`CanvasItem`，`color` 属性）；
  - 实体可视化 → `Sprite2D`（程序化单色 texture）或 `ColorRect`；
  - 确需网格时用 `MeshInstance2D.mesh`（QuadMesh）但材质改用 `CanvasItemMaterial`（`_add_sub_resource("CanvasItemMaterial", {...})`），**不用 `StandardMaterial3D`**。
- **为什么命中核心**：直接决定生成的 `.tscn` 能否在 Godot 里正常打开渲染。
- **验收**：`godot --headless --import` + `godot --headless --quit`（或跑一次主场景）对 `scenes/` 5 个场景无报错；单测补一条断言"2D 场景不产 `StandardMaterial3D`"。

### P0-2 运行时冒烟测试（"可运行"闭环）

- **做什么**：在生成收尾新增 `runtime_smoke_test` 步骤：
  1. 复用 `GodotEditor.render_screenshot_frame`（已具备 headless load 场景 + 推进 N 帧 + 出 PNG）或 `check_scripts` 之上，`godot --headless --script` 跑主场景 warmup 帧；
  2. 抓 stderr 的**运行时错误**（`SCRIPT ERROR`/`RuntimeError`/`Failed to load` 之外的红字）与**黑屏检测**（复用 `screenshot_gpu.WindowCapture.is_black`）；
  3. 产出 `{"runnable": true/false, "errors": [...], "frame_png": ...}`；
  4. 失败 → 喂给 `DebuggerAgent` 修复 → 再跑（受 `debugger.max_fix_attempts` 约束）；成功 → 写回 `complete` 事件 + 评测报告。
- **为什么命中核心**：把"编译通过"升级为"跑得起来"，是"稳定生成可运行游戏"的直接答案（godogen "proof over claims" 的 Godot 落地，但**不引入任何新引擎/素材依赖**）。
- **验收**：单测覆盖"冒烟失败→触发 debugger→再跑"的分支；`complete` 事件携带 `runnable` 字段。

### P1-3 前端契约修复（UI 稳定接后端）

- **做什么**：
  1. `complete` 事件由后端显式返回 `scene_path` + `scene_description`；前端 `app.js:839` 删掉 `Assets/Scenes/scene_description.json` 硬编码，改读事件字段；
  2. `categorizeFiles`/`previewFile`/`updateFileTree` 的 `.cs`→`.gd`/`.tscn`/`.tres`（图标、分类、高亮语言同步）；
  3. 后端 streaming 补发 `game_design`（game_designer 节点后）与 `task_plan`（planner 节点后），或前端改由 `phase_start` 驱动时间线（二选一，倾向后端补发最小侵入）。
- **为什么命中核心**：直接消除"UI 显示与后端产物对不上"的漂移。
- **验收**：手动跑一次生成，时间线点亮、文件树分类正确、试玩按钮出现。

### P1-4 契约冒烟测试

- **做什么**：用 FastAPI `TestClient` 写轻量契约测试，锁死关键字段/路径（`complete` 事件字段、`scene_path` 前缀 `scenes/`、`code_file` 的 `file_path`/`content`、错误结构）。
- **为什么命中核心**：防契约再漂移，是"稳定接后端"的长期护栏。
- **验收**：`pytest tests/unit/test_api_contract.py` 通过，且 CI（若有）纳入。

### P2-5 LLM 启动探活（轻量）

- **做什么**：lifespan 启动时对 `llm.default_model` 做一次极轻探活（可选，`GET /models` 或空 prompt）；失败则日志 `warn` 并在根 `/` 端点标注 `llm_configured=false`，前端启动时提示"请检查 API Key"。
- **为什么**：401 是真实失败源（日志 2026-08-14 出现），但不影响离线降级。

---

## 4. 执行顺序与验证

```
P0-1 修 builder →  P0-2 冒烟测试 →  P1-3 契约修复 →  P1-4 契约测试 →  P2-5 探活
（每步改完跑 pytest，再进下一步）
```

- 每一步：改代码 → 跑 `pytest tests/unit/ -q` → 必要时用 `godot --headless --import` 验证场景。
- 全程遵守项目既有约定（`src.` 前缀导入、`config.yaml` 结构、`_resolve_env`/`_normalize_godot_path`、防泄露核查）。

## 5. 明确不做（偏离核心）

- ❌ 多引擎支持（Bevy/Babylon）。
- ❌ godogen 的 3D 素材管线（Tripo3D/骨骼/视频精灵）——超出"Godot GDScript 生成"定位。
- ❌ 主 UI 实时 preview 轮询（脆弱，等 P0/P1 稳了再评估）。
