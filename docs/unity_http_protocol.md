# Unity Editor HTTP Server Protocol

GameForge Python后端通过HTTP与Unity Editor通信。Unity Editor需要运行一个HTTP服务器插件来接收请求。

## 服务器配置

- **默认地址**: `http://localhost:8765`
- **协议**: HTTP/1.1, JSON
- **Content-Type**: `application/json`

## API端点

### 1. 健康检查

```
GET /api/health
```

**响应**:
```json
{
  "status": "ok",
  "version": "1.0.0",
  "unity_version": "2022.3.x"
}
```

### 2. 导入代码文件

```
POST /api/import
```

**请求**:
```json
{
  "files": {
    "Assets/Scripts/Player/PlayerController.cs": "using UnityEngine;\n...",
    "Assets/Scripts/Core/GameManager.cs": "using UnityEngine;\n..."
  }
}
```

**响应**:
```json
{
  "status": "success",
  "imported": ["Assets/Scripts/Player/PlayerController.cs"],
  "failed": []
}
```

**Unity端行为**:
- 将每个文件写入Unity项目对应路径
- 触发`AssetDatabase.Refresh()`导入资源
- 等待编译完成后返回

### 3. 编译脚本

```
POST /api/compile
```

**响应（编译中）**:
```json
{
  "status": "compiling"
}
```

**响应（成功）**:
```json
{
  "status": "success",
  "errors": [],
  "warnings": []
}
```

**响应（有错误）**:
```json
{
  "status": "error",
  "errors": [
    {
      "file": "Assets/Scripts/Player/PlayerController.cs",
      "line": 15,
      "column": 10,
      "code": "CS0246",
      "message": "The type or namespace 'X' could not be found"
    }
  ],
  "warnings": [
    {
      "file": "Assets/Scripts/Core/GameManager.cs",
      "line": 42,
      "code": "CS0649",
      "message": "Field is never assigned to"
    }
  ]
}
```

**Unity端行为**:
- 调用`CompilationPipeline.compilationFinished`等待编译完成
- 解析编译错误和警告
- 错误格式遵循Unity标准: `file(line,column): error CSxxxx: message`

### 4. 获取编译错误

```
GET /api/compile/errors
```

**响应**: 同 `POST /api/compile` 的错误响应格式。

### 5. 生成场景

```
POST /api/scene/generate
```

**请求**:
```json
{
  "scene_name": "GameScene",
  "new_scene": true,
  "camera": {
    "position": [0, 1, -10],
    "orthographic": true,
    "orthographic_size": 6,
    "background_color": [0.4, 0.7, 1.0, 1.0]
  },
  "game_objects": [
    {
      "name": "Player",
      "type": "Sprite",
      "position": [0, 1, 0],
      "tag": "Player",
      "layer": 0,
      "sprite": "character_purple_idle",
      "components": [
        {
          "type": "Rigidbody2D",
          "properties": {
            "mass": "1",
            "gravityScale": "2",
            "freezeRotation": "true"
          }
        },
        {
          "type": "BoxCollider2D",
          "properties": {
            "size": "[0.8, 0.9]"
          }
        },
        {
          "type": "PlayerController",
          "properties": {
            "moveSpeed": "5",
            "jumpForce": "10"
          }
        }
      ]
    }
  ],
  "sprite_aliases": {
    "character_purple_idle": "player"
  }
}
```

**响应（编译中）**:
```json
{
  "status": "pending"
}
```

**响应（成功）**:
```json
{
  "status": "success",
  "scene_path": "Assets/Scenes/GameScene.unity",
  "object_count": 5
}
```

**响应（失败）**:
```json
{
  "status": "error",
  "error": "描述错误信息"
}
```

## 场景描述格式 (SceneDescription Schema)

### 顶层字段

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| scene_name | string | 是 | 场景名称 |
| new_scene | bool | 否 | 是否创建新场景（默认true） |
| camera | object | 否 | 相机配置 |
| lighting | object | 否 | 灯光配置 |
| game_objects | array | 是 | 游戏对象列表 |
| sprite_aliases | object | 否 | sprite别名映射 |

### camera对象

| 字段 | 类型 | 说明 |
|------|------|------|
| position | [x, y, z] | 相机位置 |
| orthographic | bool | 是否正交模式 |
| orthographic_size | float | 正交大小 |
| background_color | [r, g, b, a] | 背景颜色（0-1） |

### game_objects元素

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| name | string | 是 | 对象名称 |
| type | string | 是 | 对象类型（Sprite/Empty/Canvas等） |
| position | [x, y, z] | 否 | 位置（默认[0,0,0]） |
| rotation | [x, y, z] | 否 | 旋转（默认[0,0,0]） |
| scale | [x, y, z] | 否 | 缩放（默认[1,1,1]） |
| tag | string | 否 | Tag标签 |
| layer | int | 否 | Layer层级 |
| sprite | string | 否 | Sprite资源名 |
| is_static | bool | 否 | 是否静态 |
| components | array | 否 | 组件列表 |

### components元素

| 字段 | 类型 | 说明 |
|------|------|------|
| type | string | 组件类型全名（如Rigidbody2D, BoxCollider2D, 或自定义脚本类名） |
| properties | object | 组件属性键值对（均为字符串） |

## Unity端C#插件实现要点

```csharp
// 1. 使用HttpListener监听端口
private HttpListener listener;

// 2. 在EditorApplication.update中轮询请求
private void Update()
{
    if (listener.IsListening && listener.GetContext() is var context)
    {
        ProcessRequest(context);
    }
}

// 3. 编译状态检测
private bool IsCompiling()
{
    return EditorApplication.isCompiling;
}

// 4. 编译错误获取
private CompilerMessage[] GetCompileErrors()
{
    return CompilationPipeline.GetAssemblies()
        .SelectMany(a => CompilationPipeline.GetAssemblyMessages(a))
        .Where(m => m.type == CompilerMessageType.Error)
        .ToArray();
}

// 5. 场景构建
private void BuildScene(SceneDescription desc)
{
    if (desc.new_scene)
        EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
    
    foreach (var obj in desc.game_objects)
        CreateGameObject(obj);
    
    EditorSceneManager.SaveScene(
        SceneManager.GetActiveScene(),
        $"Assets/Scenes/{desc.scene_name}.unity"
    );
}
```

## 错误码

| HTTP状态码 | 说明 |
|-----------|------|
| 200 | 成功 |
| 400 | 请求格式错误 |
| 500 | Unity内部错误 |
| 503 | Unity正在编译，稍后重试 |
