# M6 · Godot 引擎集成调研：对接与串联方案

> **调研范围**：`src/engine/godot/`（17 个文件 6402 行）+ `src/agents/scene_generator/` + `src/api/main.py` 的预览与导出端点 + `addons/gameforge/`（10 个 GDScript 文件）。
>
> **一句话结论**：引擎层**自身质量不差**（超时/进程清理/空闲停止都做了，`godot_user_env` 已是单点），但**与体系的接缝有四种答法**——同一个"项目目录在哪"有三个答案、"引擎在哪"复制粘贴了六遍，其中 **scene_generator 一处还带着我已修掉的"写穿仓库"同源 bug，且当前活跃**。串联方案是收敩出一个 `GodotSession` 单点。

---

## 1. 这一层是什么

Python 侧驱动 Godot 做所有"真实引擎"活的适配层：编译校验、跑场景、渲染截图、试玩回放、出包。

## 2. 三条通路：两条已死，一条独大

| 通路 | 实现 | 现状 |
|---|---|---|
| **headless CLI** | 直接 `subprocess` 调 `godot --headless` | ✅ **主力**，9 处 spawn 点全走这条 |
| **HTTP 8765 编辑器插件** | `godot_http_client.py`（195 行）→ `http_server.gd`（395 行） | ⚠️ 半死：**要求用户手动开 Godot 编辑器并启用插件** |
| **WebSocket 8766** | `godot_ws_client.py`（197 行）→ `websocket_client.gd`（181 行） | ❌ 全死：`grep` 全仓库**零调用方** |

### 2.1 但插件不能全删——6 个 .gd 是 headless 的核心依赖

| 文件 | 行数 | 谁在用 |
|---|---|---|
| `preview_runner.gd` | 78 | `supervisor.py:7` 长驻渲染进程加载器 |
| `screenshot_server.gd` | 256 | `supervisor.py:7` 8769 截帧服务 |
| `screenshot_scene.gd` | 138 | `__init__.py:547` 一次性渲染脚本 |
| `playtest_recorder.gd` | 186 | `playtest.py:55` 试玩录制器 |
| `syntax_check.gd` | 47 | `__init__.py:314` 语法校验 |
| `settings.gd` | 73 | `supervisor.py:366` 配置读取 |

**可删的只有 4 个**：`plugin.gd`(53) / `http_server.gd`(395) / `websocket_client.gd`(181) / `ui_panel.gd`(289) = **918 行**，加上 Python 侧 `godot_http_client.py`(195) + `godot_ws_client.py`(197) = **392 行**，共 **1310 行**服务于人肉开编辑器这个场景。

> 附带收益：`project.godot` 里 `enabled=PackedStringArray("gameforge")` 可去掉，仓库作为 Godot 工程打开时不再挂一个对用户无用的面板。

---

## 3. 对接现状：四条链路，两套寻址 ★核心

| 调用方 | 怎么找项目目录 |
|---|---|
| workflow（编译/冒烟/playtest） | `paths.project_dir(pid)` ✅ 契约 |
| evaluate_run（评测） | `paths.resolve_project()` ✅ 契约 |
| **API 预览端点** | `_resolve_preview_project()` → `PROJECTS_ROOT/<id>` ⚠️ 自己拼 |
| **scene_generator** | `config godot.project_path` → `GODOT_PROJECT_PATH` → **`os.getcwd()`** ❌ |

`editor_path` 的解析**复制粘贴了 6 遍**：

```
api/main.py:993 / api/main.py:1152 / supervisor.py:130
__init__.py:180 / __init__.py:294 / runtime_smoke.py:129 / playtest.py:124
```

每处都是同一句 `config.get("godot").get("editor_path","") or os.getenv("GODOT_EDITOR_PATH","")`。

### 3.1 🔴 活跃 bug：scene_generator 会写到 cwd

`src/agents/scene_generator/__init__.py:96-100`：

```python
godot_config = self.config.get("godot", {})
project_path = godot_config.get("project_path", "")
if not project_path or project_path.startswith("${"):
    project_path = os.getenv("GODOT_PROJECT_PATH", os.getcwd())   # ← 回落 cwd！
```

**与我在 workflow 侧修掉的“写穿仓库”是同一根因，但这一处没修。** 当前 `.env` 里 `GODOT_PROJECT_PATH=` 为空，所以这条路径**此刻就是活跃风险**——scene_generator 落盘 `.tscn` 时会写到进程的工作目录（仓库根）。

已确认无测试锁定该行为（`test_scene_generation.py` / `test_headless_compile.py` 均未断言 cwd 回落），改动安全。

### 3.2 `GODOT_PROJECT_PATH` 本身是个陷阱变量

| 取值 | 后果 |
|---|---|
| 设置了 | 覆盖项目寻址，生成物写到别处（评测/预览都找不到 → M6-01 类问题） |
| 不设置 | scene_generator 掉到 `os.getcwd()` → 写脏仓库 |
| 指向仓库根 | 覆盖已跟踪文件（我实测过，回滚了） |

**三种取法三种错法，建议直接废弃。**

---

## 4. 串联方案：收敩出一个 GodotSession ★核心

### 4.1 目标形态

新建 `src/engine/godot/session.py`，**全项目只有它知道三个答案**：

```python
class GodotSession:
    """Godot 引擎对接的唯一入口。三个问题只有这里能回答。"""

    @staticmethod
    def executable(config: Dict) -> str:
        """引擎可执行文件路径。
        优先级：config.godot.editor_path → GODOT_EDITOR_PATH → 常见安装位置自动发现。
        自动发现覆盖 macOS /Applications/Godot.app/Contents/MacOS/Godot、
        Linux /usr/local/bin/godot、Windows 常见安装目录。
        """

    @staticmethod
    def project_dir(config: Dict, project_id: str) -> Path:
        """项目目录。只走 src.core.paths 契约，没有 env / cwd 兜底。
        优先级：sandbox task_dir（若启用）> paths.project_dir(project_id)。
        """

    @staticmethod
    def env(base: Optional[Dict] = None) -> Dict:
        """子进程环境变量（已实现的 godot_user_env 迁进来）。
        探测 user:// 数据目录可写性，不可写则回退 data/godot_home。
        """
```

### 4.2 迁移对照表

| 现在 | 改成 |
|---|---|
| `api/main.py:993/1152` 两处 `editor_path` 解析 | `GodotSession.executable(config)` |
| `supervisor.py:130`、`__init__.py:180/294`、`runtime_smoke.py:129`、`playtest.py:124` 六处 | 同上 |
| `__init__.py:183/297`、`runtime_smoke.py:132`、`playtest.py:127` 的 `project_path` 解析 | `GodotSession.project_dir(config, pid)` |
| **`scene_generator:96-100` 的 `env or cwd`** | **`GodotSession.project_dir(config, pid)`** ← 修活跃 bug |
| `workflow._godot_project_config()` / `_sandbox_project_config()` | 内部改用 `GodotSession.project_dir()`，保留对外签名 |
| `api._resolve_preview_project()` | 内部改用 `paths.resolve_project()`，与契约统一 |

### 4.3 分四步走，每步可独立回滚

**Step 1：修活跃 bug（最小，先做）**
- 只改 `scene_generator:96-100`：把 `os.getenv("GODOT_PROJECT_PATH", os.getcwd())` 换成从 `state` 取 `project_id` 走 `paths.project_dir()`
- scene_generator 目前拿不到 `project_id`，需要从 `state["project_context"]` 或 `state["preview_project_id"]` 传入（`workflow._resolve_preview_project_id` 已有实现，可提出来复用）
- **验收**：`GODOT_PROJECT_PATH` 留空、从仓库根启动服务，生成的 `.tscn` 落在 `projects/<pid>/scenes/`，仓库 `git status` 干净

**Step 2：抽出 session.py，迁 editor_path**
- 新建 `session.py`，把 6 处 `editor_path` 解析换掉；`godot_user_env()` 一并迁入（保持 9 处 spawn 点行为不变）
- 加常见安装位置自动发现（消掉 M6-05 配置门槛）
- **验收**：`GODOT_EDITOR_PATH` 不设置时，macOS 能自动找到 `/Applications` 或仓库内 `tools/godot/`；全量回归 583 不变

**Step 3：迁 project_path，废弃 GODOT_PROJECT_PATH**
- 剩余 4 处 `project_path` 解析换掉；`.env` 与 `.env.example` 删除 `GODOT_PROJECT_PATH`，`config.yaml` 删除 `godot.project_path`
- **验收**：`grep -rn "GODOT_PROJECT_PATH" src/ config/ .env.example` 为空；删除该变量后全链路仍正常

**Step 4：清理死代码（可选，最后做）**
- 删 `godot_ws_client.py`(197) + `websocket_client.gd`(181) —— 零调用方
- 删 `http_server.gd`(395) + `ui_panel.gd`(289) + `plugin.gd`(53) + `godot_http_client.py`(195) —— 仅 8765 通路用
- `project.godot` 去掉 `editor_plugins.enabled` 里的 `gameforge`
- **验收**：`grep -rn "GodotHTTPClient\|GodotWSClient" src/` 仅剩 `session.py` 或为空；工作流、预览、导出三条链路实测仍通

---

## 5. 问题清单（供汇总）

| 编号 | 问题 | 严重度 | 状态 |
|---|---|---|---|
| M6-01 | **scene_generator 落盘回落 `os.getcwd()`，当前活跃的写穿仓库风险** | **高** | 未处理 |
| M6-02 | WebSocket 8766 整套 378 行死代码 | 中 | 未处理 |
| M6-03 | HTTP 8765 通路要求人肉开编辑器，headless 时代已无价值 | 中 | 未处理 |
| M6-04 | 编辑器插件 + Python 客户端共 **1310 行**服务于此 | 中 | 未处理 |
| M6-05 | `GODOT_PROJECT_PATH` 三值三错，是个陷阱变量 | 中 | 未处理 |
| M6-06 | `editor_path` 解析复制 6 遍、`project_path` 解析 4 套 | 中 | 未处理 |
| M6-07 | 引擎路径无自动发现，配置门槛高（macOS 要填到 .app 内） | 低 | 未处理 |
| M6-08 | 渲染进程真实执行项目代码（与 M5-03 同源，汇总应合并） | **高** | 未处理 |

## 6. 串联的收益

1. **消掉当前活跃的写穿仓库风险**（M6-01）
2. `GODOT_PROJECT_PATH` / `godot.project_path` 这两个变量**彻底消失**，少两个陷阱
3. 新接入方（比如以后加"关卡编辑器"）只需要 `GodotSession` 一个入口，不会再出现第四种寻址方式
4. 死代码清理有了抓手（-1310 行）
5. `src/core/paths.py` 的路径契约从"靠人遵守"变成**唯一实现**

---

## 附：验证方式

```bash
# M6-01：scene_generator 的 cwd 回落（当前活跃）
sed -n '92,102p' src/agents/scene_generator/__init__.py

# M6-06：重复的解析逻辑
grep -rn 'GODOT_PROJECT_PATH\|get("editor_path"' src/ --include=*.py

# M6-02：WS 客户端零调用方
grep -rn "GodotWSClient\|godot_ws_client" src/ --include=*.py | grep -v "godot_ws_client.py:"

# M6-03：8765 HTTP 的调用方
grep -rn "GodotHTTPClient" src/ --include=*.py | grep -v "godot_http_client.py:"

# 串联后应满足的契约
grep -rn "GODOT_PROJECT_PATH" src/ config/ .env.example   # 目标：为空
grep -rn '"projects"' src/ --include=*.py | grep -v "core/paths.py"   # 目标：为空
```
