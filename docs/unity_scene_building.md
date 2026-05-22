# Unity 场景自动构建指南

GameForge 支持两种工作模式：**离线模式**（默认）和 **Unity 自动构建模式**。

## 离线模式（默认）

默认配置下，GameForge 不要求 Unity Editor 运行。生成结果始终包含：

| 文件 | 说明 |
|------|------|
| `Assets/Scenes/scene_description.json` | Unity 场景描述 JSON |
| `Assets/GameDesignModel.json` | 游戏设计模型 |
| `Assets/CodeMetadata.json` | 代码元数据 |
| `Assets/README_Unity.md` | 项目说明文档 |
| `Assets/ProjectSettings_Suggestions.md` | Unity 项目设置建议 |
| `Assets/Editor/GameForgeHttpServer.cs` | Unity HTTP Server 插件 |

前端会显示"场景描述已生成，Unity 自动构建已跳过"，不会显示错误。

## Unity 自动构建模式

当 `unity.auto_build_scene=true` 且 Unity Editor HTTP Server 运行时，GameForge 会：

1. 生成场景描述 JSON
2. 将生成的 `.cs` 代码文件导入 Unity 项目
3. 调用 Unity 创建 `.unity` 场景文件
4. 触发编译并返回编译结果

### 启用步骤

#### 1. 在 Unity 中导入 HTTP Server 插件

将生成的 `Assets/Editor/GameForgeHttpServer.cs` 文件保留在 Unity 项目中（GameForge 会自动输出此文件）。

插件依赖 `Newtonsoft.Json`（Unity 2020+ 自带）。如果编译报错，在 Unity Package Manager 中安装 `com.unity.nuget.newtonsoft-json`。

#### 2. 启动 HTTP Server

在 Unity Editor 菜单中：

```
GameForge → Start HTTP Server
```

控制台会显示：
```
[GameForge] HTTP Server started on http://localhost:8765
```

验证服务器运行：
```bash
curl http://localhost:8765/api/health
# 返回: {"status":"ok","version":"1.0.0","unity_version":"2022.3.x"}
```

#### 3. 修改配置

编辑 `config/config.yaml`：

```yaml
unity:
  auto_build_scene: true    # 开启自动构建
  http_port: 8765           # 与 Unity HTTP Server 端口一致
```

#### 4. 重新生成

再次提交需求时，GameForge 会自动连接 Unity 并构建场景。

## API 端点

Unity HTTP Server 实现以下端点：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| POST | `/api/import` | 导入代码文件 |
| POST | `/api/scene/generate` | 创建 Unity 场景 |
| POST | `/api/compile` | 触发编译 |
| GET | `/api/compile/errors` | 获取编译错误 |

详细协议参见 [unity_http_protocol.md](unity_http_protocol.md)。

## 常见问题

### Q: 为什么 Python 后端不能直接创建 .unity 场景文件？

Unity 的 `.unity` 场景文件是 YAML 格式的序列化文件，包含 Unity 内部的对象引用、GUID、组件序列化数据等。这些内容只能由 Unity Editor 内部的 API（`EditorSceneManager`、`GameObject`、`Component` 等）正确生成。Python 后端通过 HTTP 调用 Unity Editor 插件来执行场景构建。

### Q: auto_build_scene=false 时生成的代码能用吗？

可以。所有代码文件（`.cs`）和场景描述（`scene_description.json`）都会输出。你可以：
1. 手动将 `.cs` 文件复制到 Unity 项目的 `Assets/Scripts/` 目录
2. 在 Unity 中使用 `GameForge → Start HTTP Server` 启动服务器
3. 后续生成时开启 `auto_build_scene=true` 即可自动构建

### Q: Unity 显示 "Tag 'Player' not defined" 怎么办？

在 Unity 中打开 `Edit → Project Settings → Tags and Layers`，添加需要的 Tag（如 `Player`、`Enemy`）。GameForge 生成的 `ProjectSettings_Suggestions.md` 会列出代码中引用的所有 Tag 和 Layer。

### Q: 编译错误会自动修复吗？

是的。当 `auto_build_scene=true` 时，GameForge 会执行编译闭环：
1. 导入代码 → 编译
2. 如果有编译错误，Debugger Agent 会分析错误并生成修复
3. 重新导入修复后的代码 → 重新编译
4. 最多 3 轮修复
