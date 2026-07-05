# GameForge Demo — Godot 4.x 项目

这是一个由 GameForge AI 生成的 Godot 4.x 演示项目。

## 项目结构

```
├── project.godot          # 项目配置
├── scenes/
│   └── Main.tscn          # 主场景
├── scripts/
│   ├── player.gd          # 玩家角色脚本
│   ├── enemy.gd           # 敌人脚本
│   ├── coin.gd            # 金币脚本
│   └── hud.gd             # HUD 脚本
├── autoload/
│   └── game_manager.gd    # 全局游戏管理器
├── components/            # 可复用组件
├── addons/
│   └── ai_native/         # AI 原生工具（待扩展）
└── assets/                # 资源文件
```

## 运行方式

1. 用 Godot 4.x 打开 `project.godot`
2. 按 F5 运行游戏

## 操作说明

- **A/D** 或 **方向键左右**：移动
- **空格**：跳跃
- **ESC**：暂停

## 游戏玩法

- 控制角色收集金币
- 每个金币 +10 分
- 避开敌人（待实现碰撞伤害）
- 3 条生命，用完游戏结束

## 待完善

- [ ] 添加 AnimatedSprite2D 帧动画
- [ ] 添加 CollisionShape2D 碰撞形状
- [ ] 实现敌人伤害逻辑
- [ ] 添加音效
- [ ] 添加更多关卡
