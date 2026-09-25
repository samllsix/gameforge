# 实时预览 2.0：让前端看到真实 Godot 游戏画面

**状态**：设计文档 v1（待评审）
**作者**：GameForge
**日期**：2026-08-21
**对应代码版本**：demo_jump_v2
**前置文档**：路径 B 闭环报告（Redis + Qdrant + 多智能体已就绪）

---

## 1. 背景与现状

### 1.1 当前实现（已上线 / "1.0 模拟版"）

```
浏览器 ←─fetch─► FastAPI(:8768) ←─subprocess.run─► Godot --headless --script screenshot_scene.gd
                                                          │
                                                          ├─ load res://scenes/main.tscn
                                                          ├─ SubViewport 640x360
                                                          └─ save_png → 落盘
```

**问题**：`--headless` 默认启用 `dummy` 渲染器，`Viewport.get_texture().get_image()` 返回空。
当前靠 Godot 脚本 `_build_placeholder_image()` 程序化生成占位 PNG（顶部青色条 + 底部紫色条 + 中心白色滑条）维持链路连通。

**前端表现**：每 1200ms 拉一帧，TAG 从 `SIM` 切到 `LIVE`，但**画面内容是静态占位图**，不是游戏。

### 1.2 设计目标（"2.0 真渲染版"）

| 目标 | 度量 |
|---|---|
| 浏览器看到的是 Godot 实际渲染的游戏画面 | drawImage(image) 与编辑器 F5 看到的一致 |
| 延迟可接受 | 单次截图 < 200ms；连续帧间隔 200-500ms |
| 进程崩溃可自愈 | Godot 退出后下次请求自动重启 |
| 鉴权 | 仅 loopback + 内部 token，不暴露公网 |
| 不抢占用户桌面 | Godot 窗口默认隐藏到屏幕外/最小化 |
| 跨平台预留 | 接口抽象化，Linux/Xvfb 可替换底层 |

---

## 2. 架构总览

```
┌──────────────┐   HTTP(GET /api/v1/preview/frame)   ┌──────────────┐
│   浏览器      │  ──────────────────────────────►   │   FastAPI     │
│  (驾驶舱)     │  ◄────── image/png ─────────────   │   :8768       │
│              │                                    │              │
│ drawImage()  │                                    │ GodotSupervisor│
│ 每 250ms     │                                    │   ├─ Process  │
│ 轮询         │                                    │   ├─ Health  │
└──────────────┘                                    │   └─ Token   │
                                                    └──────┬───────┘
                                                           │ spawn / supervise
                                                           ▼
                                                   ┌──────────────┐
                                                   │  Godot 4.6   │
                                                   │  (带窗口，     │
                                                   │   屏幕外)     │
                                                   │              │
                                                   │ ScreenshotPlugin│
                                                   │   ├─ TCPServer:8769
                                                   │   ├─ /screenshot
                                                   │   ├─ /health    │
                                                   │   └─ /frame/advance
                                                   └──────────────┘
```

**关键变化**：

| 1.0 | 2.0 |
|---|---|
| 每次请求 spawn 一个 Godot 进程 | Godot 进程**常驻**，按需截图 |
| Godot `--headless` dummy 渲染器 | Godot 带窗口运行（屏幕外）+ Vulkan/OpenGL 真渲 |
| Python 通过 subprocess stdout 拿 PNG | Godot 暴露 8769 HTTP，Python 反向 GET |
| 单帧截图脚本 `screenshot_scene.gd` | 长驻服务 `screenshot_server.gd` |
| 进程冷启 1-2s/帧 | 单次截图 < 200ms |

---

## 3. 进程生命周期

### 3.1 启动时序

```
t=0    supervisor.start()  ── Popen(godot.exe --path projects/demo_jump_v2 ...)
t=0~   Godot 启动编辑器/Player，加载 project.godot + 第一个 .tscn
t=2s   Godot 加载完 addon:gameforge/screenshot_server.gd
       ── TCPServer.listen(127.0.0.1:8769) → 端口就绪
t=2.1s Python 第一次 /api/v1/preview/frame 请求
       → supervisor.get_frame(frame=0) → http://127.0.0.1:8769/screenshot?frame=0
       → 拿到 PNG → 返回浏览器
```

### 3.2 运行期状态机

```
[STOPPED] ── start()──► [STARTING] ──port ok──► [READY]
                                                    │
                                          ┌───GET /screenshot───┐
                                                  ▼
                                              [BUSY] ──done──► [READY]
                                          └──fail───┘
                                                  ▼
                                              [FAILED]
                                                  │
                                       supervisor detects crash
                                                  ▼
                                              [RESTARTING] (backoff 1s,3s,9s)
                                                  │
                                                端口 ok ──► [READY]
```

### 3.3 自愈规则

| 异常 | 处理 |
|---|---|
| Godot 进程退出码非 0 | 标记 FAILED，5 秒后重启 |
| 端口 8769 不可达 | TCP 连接探测失败 3 次 → 重启 Godot |
| 单次截图超时（>5s）| kill Godot 进程 → 重启 |
| 浏览器主动断开 | 不影响（无状态） |
| 同一进程累计 >30 分钟 | 自动滚动重启，释放内存 |

### 3.4 关闭时

```
FastAPI 收到 SIGTERM
  → supervisor.stop_all() 遍历所有 project
    → godot_proc.terminate() 优雅退出
    → 5s 后 godot_proc.kill() 强杀
  → 释放端口
```

---

## 4. Godot 端插件（screenshot_server.gd）

### 4.1 文件

| 文件 | 作用 |
|---|---|
| `addons/gameforge/screenshot_server.gd` | 新增：TCPServer 实现 8769 HTTP 端点 |
| `addons/gameforge/screenshot_server.gd.uid` | 自动生成 |
| `addons/gameforge/config.cfg` | 改：增加 `screenshot_port = 8769` 与 `screenshot_token` |
| `projects/<pid>/project.godot` | 不需要改（addon 自动加载） |

### 4.2 关键模块

```gdscript
## addons/gameforge/screenshot_server.gd
@tool
extends Node

const PORT_DEFAULT := 8769
const TOKEN_DEFAULT := "gf_screenshot_local"

var tcp_server: TCPServer
var port: int = PORT_DEFAULT
var token: String = TOKEN_DEFAULT
var current_frame: int = 0
var simulated_dt: float = 0.0  ## 累积的虚拟时间
var main_scene: Node = null    ## 加载进来的场景根

func _ready() -> void:
    var settings = preload("res://addons/gameforge/settings.gd").new()
    port = int(settings.get_value("screenshot_port", PORT_DEFAULT))
    token = str(settings.get_value("screenshot_token", TOKEN_DEFAULT))
    tcp_server = TCPServer.new()
    var err := tcp_server.listen(port, "127.0.0.1")
    if err != OK:
        push_error("[screenshot_server] bind %d failed: %d" % [port, err])
        return
    print("[screenshot_server] listening on 127.0.0.1:%d" % port)

func _process(delta: float) -> void:
    if tcp_server == null: return
    if not tcp_server.is_connection_available(): return
    var conn := tcp_server.take_connection()
    if conn == null: return
    _handle(conn)

func _handle(conn: StreamPeerTCP) -> void:
    var raw := ""
    while conn.get_available_bytes() > 0:
        raw += conn.get_utf8_string(conn.get_available_bytes())
        if raw.find("\r\n\r\n") >= 0: break
    if raw.is_empty():
        conn.disconnect_from_host(); return

    var request_line := raw.split("\r\n", true, 1)[0]
    var parts := request_line.split(" ")
    if parts.size() < 2:
        conn.disconnect_from_host(); return
    var path := parts[1].get_slice("?", 0)

    if path == "/health":
        _send_json(conn, 200, {"status": "ok", "engine": "godot",
            "version": Engine.get_version_info().string})
        return

    if not _check_token(raw):
        _send_json(conn, 401, {"error": "unauthorized"}); return

    var query := parts[1].get_slice("?", 1) if "?" in parts[1] else ""
    var frame := _parse_query_int(query, "frame", current_frame)

    if path == "/screenshot":
        current_frame = frame
        var img := _capture()
        _send_png(conn, img)
        return

    if path == "/frame/advance":
        current_frame = frame
        _send_json(conn, 200, {"frame": current_frame})
        return

    _send_json(conn, 404, {"error": "not found", "path": path})

func _capture() -> Image:
    ## 把 _process 累积的 dt 推到目标帧对应时刻
    while int(simulated_dt * 60.0) < current_frame:
        simulated_dt += 1.0 / 60.0
    var tex := get_viewport().get_texture()
    if tex == null:
        return _placeholder(640, 360)
    return tex.get_image()

func _placeholder(w: int, h: int) -> Image:
    var img := Image.create(w, h, false, Image.FORMAT_RGBA8)
    img.fill(Color(0.05, 0.05, 0.1, 1.0))
    return img

func _send_json(conn: StreamPeerTCP, status: int, body: Dictionary) -> void:
    var json := JSON.stringify(body)
    var resp := "HTTP/1.1 %d %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" % [
        status, "OK" if status == 200 else "Error", json.length(), json]
    conn.put_data(resp.to_utf8_buffer())
    conn.disconnect_from_host()

func _send_png(conn: StreamPeerTCP, img: Image) -> void:
    var tmp := "user://_gf_frame.png"
    img.save_png(tmp)
    var bytes := FileAccess.get_file_as_bytes(tmp)
    if bytes.is_empty():
        _send_json(conn, 500, {"error": "empty png"}); return
    var header := PackedStringArray([
        "HTTP/1.1 200 OK",
        "Content-Type: image/png",
        "Content-Length: %d" % bytes.size(),
        "Cache-Control: no-store",
        "X-Preview-Frame: %d" % current_frame,
        "",
        ""
    ]).join("\r\n")
    conn.put_data(header.to_utf8_buffer())
    conn.put_data(bytes)
    conn.disconnect_from_host()

func _check_token(raw: String) -> bool:
    if token.is_empty(): return true
    for line in raw.split("\r\n"):
        if line.to_lower().begins_with("x-api-key:"):
            return line.substr(11).strip_edges() == token
    return false

func _parse_query_int(query: String, key: String, default: int) -> int:
    for kv in query.split("&"):
        if kv.begins_with(key + "="):
            return int(kv.substr(key.length() + 1))
    return default
```

### 4.3 窗口隐藏

启动 Godot 时使用：

```bash
godot.exe --path projects/demo_jump_v2 \
          --resolution 640x360 \
          --position 9999,9999
```

`--position 9999,9999` 让窗口出现在主屏幕外（左上），用户不可见。
若机器只有单屏且不可移到屏幕外，**额外方案**：Godot 端启动后调用 `DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_MINIMIZED)`。

---

## 5. 后端：GodotSupervisor

### 5.1 文件

| 文件 | 作用 |
|---|---|
| `src/engine/godot/supervisor.py` | 新增：进程管理器 |
| `src/engine/godot/__init__.py` | 改：暴露 `Supervisor` |
| `src/api/main.py` | 改：`/api/v1/preview/frame` 调 supervisor |
| `config/config.yaml` | 改：增加 `preview:` 段 |

### 5.2 配置

```yaml
# config/config.yaml 新增段
preview:
  enabled: true
  screenshot_port: 8769
  screenshot_token_env: GAMEFORGE_PREVIEW_TOKEN   # 不设置时用默认值
  default_token: "gf_screenshot_local"
  startup_timeout_seconds: 8
  request_timeout_seconds: 5
  health_check_interval_seconds: 3
  max_process_age_seconds: 1800
  restart_backoff: [1, 3, 9]
  frame_max_step: 600      # 单次最多推进 600 帧，防止卡顿后爆栈
```

### 5.3 进程管理器接口

```python
# src/engine/godot/supervisor.py
class GodotSupervisor:
    """按 project_id 缓存常驻 Godot 截图进程。线程安全。"""

    async def start(self, project_id: str, project_path: str) -> None: ...
    async def stop(self, project_id: str) -> None: ...
    async def stop_all(self) -> None: ...
    async def get_frame(self, project_id: str, frame_index: int) -> bytes: ...
    async def is_alive(self, project_id: str) -> bool: ...
```

- **进程池**：`Dict[str, ProjectProc]`，key 是 project_id
- **LRU 淘汰**：超过 `max_process_age_seconds` 自动滚动重启
- **并发**：单进程内 Godot 单线程，所以**每进程串行**，但**多项目可并行**
- **健康检查**：后台 task 每 3s 探一次 `/health`，失败 3 次 → 重启

### 5.4 /api/v1/preview/frame 新版

```python
@app.get("/api/v1/preview/frame")
async def preview_frame(
    project_id: str,
    frame: int = 0,
    width: int = 640,
    height: int = 360,
):
    # 1. 路径校验（同 1.0）
    project_path = _resolve_preview_project(project_id)
    if not Path(project_path, "project.godot").is_file():
        raise HTTPException(404, f"项目 {project_id} 缺少 project.godot")

    # 2. 确保 Godot 进程在跑
    supervisor = await GodotSupervisor.get_instance(config)
    if not await supervisor.is_alive(project_id):
        await supervisor.start(project_id, project_path)

    # 3. 拉一帧（timeout 5s）
    try:
        png_bytes = await supervisor.get_frame(project_id, frame)
    except GodotTimeout:
        await supervisor.stop(project_id)   # 让下次重新拉起
        raise HTTPException(504, "Godot 截图超时")
    except GodotCrashed:
        await supervisor.stop(project_id)
        raise HTTPException(502, "Godot 进程已崩溃，下次请求自动重启")

    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Preview-Frame": str(frame),
            "X-Preview-Timestamp": str(int(time.time())),
            "X-Preview-Source": "godot-live",
        },
    )
```

### 5.5 反向调用 Godot

```python
# supervisor.py 内部
async def _fetch_from_godot(self, port: int, frame: int) -> bytes:
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(
            f"http://127.0.0.1:{port}/screenshot?frame={frame}",
            headers={"X-API-Key": self.token},
        )
        r.raise_for_status()
        return r.content
```

---

## 6. 前端：drawPrev v2

### 6.1 改动点（最小）

| 改动 | 文件位置 |
|---|---|
| `setInterval` 1200ms → 250ms | `digital-life-system-spatial.html` |
| 显示真实画面时把 prevTag 从 `LIVE` 切到 `GAME` 并加 `.live-pulse` 动画 | 同上 |
| 失败 3 次后切回 `SIM` + 降级 | 同上 |
| 加 `X-Preview-Source` 头解析，识别 `godot-live` vs `placeholder` | 同上 |

### 6.2 拟新增 CSS

```css
.prev-box.live::after {
  content: ""; position: absolute; inset: 0;
  border: 1px solid rgba(54, 224, 170, .55);
  border-radius: 6px;
  box-shadow: 0 0 12px rgba(54,224,170,.35) inset;
  pointer-events: none;
  animation: live-pulse 1.4s ease-in-out infinite;
}
@keyframes live-pulse {
  0%,100% { opacity: .35 } 50% { opacity: .8 }
}
.prev-box.sim::after {
  content: "SIM"; ... 显示降级提示
}
```

---

## 7. 鉴权与安全

| 场景 | 措施 |
|---|---|
| 前端轮询 | FastAPI `GAMEFORGE_API_KEYS`（同现有）/ loopback 豁免 |
| Python ↔ Godot 内部 | `X-API-Key` header + 环境变量 token；token 不进 git，`.env.example` 仅占位 |
| Godot 不暴露公网 | listen `127.0.0.1`，不监听 `0.0.0.0` |
| 路径穿越 | `project_id` 严格白名单 `^[A-Za-z0-9_\-.]{1,64}$` + `Path.resolve().relative_to(projects_root)` |
| 端口占用 | 启动前 `socket.bind(('127.0.0.1', port))` 探测；占用则换 8769/8770/8771 |
| Token 轮换 | 通过 `GAMEFORGE_PREVIEW_TOKEN` env 注入，部署时 set，无需改代码 |

---

## 8. 错误处理矩阵

| 错误 | 表现 | 动作 |
|---|---|---|
| Godot 启动失败（exe 缺失） | `GET /frame` → 502 | supervisor 标记 unavailable，UI 切 `SIM`，5 分钟后再试 |
| Godot 启动超时 (>8s) | 同上 | supervisor kill 进程，下一次重试 |
| Godot 加载场景失败 | `GET /frame` → 500（stderr 在日志） | UI 切 `SIM` + Toast "Godot 场景加载失败" |
| Godot 进程崩溃（运行中） | 内部：下一次 health 失败 | 自动重启（指数退避） |
| 单帧截图超时 | `GET /frame` → 504 | kill + 下次重试 |
| 磁盘满 PNG 写不出 | Godot stderr | Godot 返回 500 + placeholder |
| 浏览器断网 | 静默 | 下次请求自动恢复 |

---

## 9. 部署/运行

### 9.1 启动命令

```powershell
# 1. 启动 Redis + Qdrant
docker start gf-redis gf-qdrant

# 2. 启动 FastAPI（supervisor 在 lifespan 内按需拉 Godot）
$env:GAMEFORGE_ENV='development'
$env:GAMEFORGE_ALLOW_INSECURE_LOCALHOST='true'
$env:GAMEFORGE_PORT='8768'
$env:GAMEFORGE_PREVIEW_TOKEN='gf_screenshot_local'   # 自定义 token
$env:GODOT_EDITOR_PATH='D:\godot\Godot_v4.6.3-stable_win64.exe\Godot_v4.6.3-stable_win64.exe'
& 'D:\game_project\.venv\Scripts\python.exe' -m uvicorn src.api.main:app --host 127.0.0.1 --port 8768

# 3. 浏览器访问 /app，实时预览画框开始轮询 Godot
```

### 9.2 健康检查

```powershell
# 全部正运行时
curl http://127.0.0.1:8768/api/v1/preview/frame?project_id=demo_jump_v2&frame=0 -o frame.png
# 预期：返回 image/png，文件头为 89 50 4E 47
Get-FileHash frame.png -Algorithm SHA256

# 直接测 Godot 端口
curl http://127.0.0.1:8769/health -H "X-API-Key: gf_screenshot_local"
# 预期：{"status":"ok","engine":"godot",...}
```

---

## 10. 迁移路径（兼容 1.0）

为防止老接口突然不能工作，保留 1.0 的行为**仅在 supervisor 不可用时**：

```python
if not supervisor_enabled or not config.preview.enabled:
    # 回退到 1.0：每次 subprocess
    return await _legacy_screenshot_frame(project_id, frame)
```

通过环境变量 `GAMEFORGE_PREVIEW_ENABLED=false` 关闭 2.0，回到 1.0 行为。

---

## 11. 实施计划

| 步骤 | 工作量 | 验证 |
|---|---|---|
| 1. Godot 端 `screenshot_server.gd` 编写 | 30 min | 手动启 Godot，`curl http://127.0.0.1:8769/health` 返回 200 |
| 2. config.yaml 加 `preview:` 段 | 5 min | `python -c "from src.utils.config import load; print(load()['preview'])"` |
| 3. `GodotSupervisor` 实现 | 1 h | 单元测试：start/stop/get_frame/restart |
| 4. `/api/v1/preview/frame` 切到 supervisor | 15 min | curl 拿到 PNG，X-Preview-Source=godot-live |
| 5. 前端 250ms 轮询 + LIVE/SIM 切换 | 15 min | 浏览器看到真实游戏画面 |
| 6. 错误注入测试（kill Godot 中途） | 30 min | 下次请求自动恢复 |
| 7. 文档更新 + 配置默认值 | 15 min | `.env.example` 加 `GAMEFORGE_PREVIEW_TOKEN` |
| **合计** | **约 4 h** | |

---

## 12. 未来增强（不在本期范围）

| 增强 | 优先级 |
|---|---|
| WebSocket 推送帧（替代轮询） | P1 |
| Linux/Xvfb + Vulkan CI 截图 | P2 |
| 多 project 并发共享同一 Godot 进程（单进程多场景） | P2 |
| 视频流（MJPEG）替代 PNG 轮询 | P3 |
| GPU 截图（Vulkan `vkGetImage`）零拷贝 | P3 |
| 实时输入注入（点击、键盘）反向到 Godot | P1（之后做交互必须） |

---

## 13. 风险与回退

| 风险 | 概率 | 影响 | 回退 |
|---|---|---|---|
| `--position 9999,9999` 在多屏下行为不一致 | 中 | 用户桌面被挡 | 切到 `--display-driver headless` + `dummy` 渲染器，退回 1.0 |
| Godot 在 8769 端口拒绝绑定 | 低 | 进程起不来 | supervisor 探测后换端口 +5001 |
| 单帧截图仍 > 200ms | 中 | UI 卡顿 | 降低轮询频率到 500ms + 把 width/height 砍半 |
| Vulkan 驱动缺失（无显卡机器） | 低 | 黑屏 | `--rendering-driver opengl3` 备选 |
| Windows 焦点冲突 | 中 | 用户切到 Godot 窗口 | `DisplayServer.window_set_mode(MINIMIZED)` + `always_on_bottom=true` |

**回退开关**：`GAMEFORGE_PREVIEW_ENABLED=false` → 全走 1.0 子进程路径，1 分钟内可恢复。

---

## 14. 验收清单

- [x] 浏览器打开 `/`（数字生命驾驶舱，旧 `/app` 已移除），任务运行期间"实时预览"框显示真实游戏画面
- [ ] 画面每 250ms 更新一次，肉眼感觉连续
- [ ] 手动 kill Godot 进程，下次请求 5 秒内恢复
- [ ] curl `/api/v1/preview/frame?project_id=evil/../etc` 返回 400
- [ ] curl `/api/v1/preview/frame?project_id=nonexistent` 返回 404
- [ ] `X-Preview-Source=godot-live` 在响应头出现
- [ ] 关闭 FastAPI 时 Godot 子进程被一起关闭，无僵尸进程
- [ ] `.env.example` 注明 `GAMEFORGE_PREVIEW_TOKEN`
- [ ] `git grep 'gf_screenshot_local' -- '*.py'` 仅命中默认值，无真实密钥

---

**评审要点**：

1. 窗口放到屏幕外（`--position 9999,9999`）是否在你的机器上生效？
2. 单 Godot 进程 vs 多进程池：当前方案是**每 project 一个进程**，可以接受吗？
3. Token 默认值 `gf_screenshot_local` 仅 loopback + env 可覆盖，安全吗？
4. 250ms 轮询是否合理？需要的话可调 100ms（更流畅）但带宽增加。
5. 长期要不要直接上 WebSocket（避免轮询浪费）？

确认后我立即开工。