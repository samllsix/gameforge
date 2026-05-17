# 2D平台跳跃游戏设计文档

## 项目概述
一个经典的2D平台跳跃游戏，玩家控制角色在各种平台间跳跃，收集物品，避开障碍物。

## 核心玩法

### 玩家控制
- 左右移动（A/D键或方向键）
- 跳跃（空格键）
- 二段跳（可选）

### 游戏元素
- **平台**: 静态和移动平台
- **收集物**: 金币、宝石
- **障碍物**: 尖刺、敌人
- **终点**: 到达终点过关

## 系统需求

### 角色系统
- PlayerController: 玩家移动和跳跃
- PlayerHealth: 玩家生命值管理
- PlayerAnimation: 动画控制

### 游戏管理
- GameManager: 游戏状态管理
- LevelManager: 关卡管理
- ScoreManager: 计分系统

### 物理系统
- CollisionHandler: 碰撞检测
- PlatformEffector: 平台效果

### UI系统
- HUD: 分数、生命值显示
- MenuUI: 菜单界面
- GameOverUI: 游戏结束界面

## 技术要求
- 引擎: Unity 2022
- 渲染: 2D Sprite
- 物理: Rigidbody2D + BoxCollider2D
- 输入: Input System (Legacy)
