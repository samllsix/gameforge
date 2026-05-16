"""GameForge - 记忆系统模块

管理Agent对话历史、项目上下文和知识检索。
"""

import json
import os
from typing import Any, Dict, List, Optional
from datetime import datetime
from collections import deque


class ConversationMemory:
    """对话记忆 - 管理单个Agent的对话历史"""

    def __init__(self, max_messages: int = 50):
        self.messages: deque = deque(maxlen=max_messages)
        self.metadata: Dict[str, Any] = {}

    def add_message(self, role: str, content: str, metadata: Optional[Dict] = None):
        """添加消息到历史

        Args:
            role: 角色（system/user/assistant）
            content: 消息内容
            metadata: 可选元数据
        """
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **(metadata or {}),
        }
        self.messages.append(message)

    def get_messages(self, last_n: Optional[int] = None) -> List[Dict[str, str]]:
        """获取消息历史

        Args:
            last_n: 获取最近N条消息

        Returns:
            消息列表
        """
        messages = list(self.messages)
        if last_n:
            messages = messages[-last_n:]
        return [{"role": m["role"], "content": m["content"]} for m in messages]

    def get_context_window(self, max_tokens: int = 4000) -> List[Dict[str, str]]:
        """获取适合LLM上下文窗口的消息

        Args:
            max_tokens: 最大token数（估算）

        Returns:
            消息列表
        """
        messages = []
        current_tokens = 0

        for msg in reversed(self.messages):
            msg_tokens = len(msg["content"]) // 4
            if current_tokens + msg_tokens > max_tokens:
                break
            messages.insert(0, {"role": msg["role"], "content": msg["content"]})
            current_tokens += msg_tokens

        return messages

    def clear(self):
        """清空对话历史"""
        self.messages.clear()

    def summary(self) -> str:
        """生成对话摘要"""
        if not self.messages:
            return "无对话历史"

        user_msgs = sum(1 for m in self.messages if m["role"] == "user")
        assistant_msgs = sum(1 for m in self.messages if m["role"] == "assistant")
        return f"共{len(self.messages)}条消息 (用户:{user_msgs}, 助手:{assistant_msgs})"


class ProjectMemory:
    """项目记忆 - 管理项目级别的上下文信息"""

    def __init__(self, storage_dir: str = "data/memory"):
        self.storage_dir = storage_dir
        self.context: Dict[str, Any] = {
            "decisions": [],
            "patterns": [],
            "errors": [],
            "learnings": [],
        }
        os.makedirs(storage_dir, exist_ok=True)

    def add_decision(self, decision: str, reason: str, impact: str = ""):
        """记录架构/设计决策

        Args:
            decision: 决策内容
            reason: 决策原因
            impact: 影响范围
        """
        self.context["decisions"].append({
            "decision": decision,
            "reason": reason,
            "impact": impact,
            "timestamp": datetime.now().isoformat(),
        })

    def add_pattern(self, pattern_name: str, description: str, example: str = ""):
        """记录代码模式

        Args:
            pattern_name: 模式名称
            description: 模式描述
            example: 示例代码
        """
        self.context["patterns"].append({
            "name": pattern_name,
            "description": description,
            "example": example,
            "timestamp": datetime.now().isoformat(),
        })

    def add_error(self, error_type: str, solution: str, context: str = ""):
        """记录错误和解决方案

        Args:
            error_type: 错误类型
            solution: 解决方案
            context: 错误上下文
        """
        self.context["errors"].append({
            "type": error_type,
            "solution": solution,
            "context": context,
            "timestamp": datetime.now().isoformat(),
        })

    def add_learning(self, topic: str, content: str):
        """记录学习到的知识

        Args:
            topic: 主题
            content: 内容
        """
        self.context["learnings"].append({
            "topic": topic,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })

    def get_relevant_context(self, query: str, max_items: int = 5) -> Dict[str, List]:
        """获取与查询相关的上下文

        Args:
            query: 查询内容
            max_items: 每类最大返回数

        Returns:
            相关上下文
        """
        query_lower = query.lower()
        relevant = {"decisions": [], "patterns": [], "errors": [], "learnings": []}

        for decision in self.context["decisions"]:
            if any(kw in decision["decision"].lower() or kw in decision["reason"].lower()
                   for kw in query_lower.split()):
                relevant["decisions"].append(decision)
                if len(relevant["decisions"]) >= max_items:
                    break

        for pattern in self.context["patterns"]:
            if query_lower in pattern["name"].lower() or query_lower in pattern["description"].lower():
                relevant["patterns"].append(pattern)
                if len(relevant["patterns"]) >= max_items:
                    break

        for error in self.context["errors"]:
            if query_lower in error["type"].lower() or query_lower in error["solution"].lower():
                relevant["errors"].append(error)
                if len(relevant["errors"]) >= max_items:
                    break

        for learning in self.context["learnings"]:
            if query_lower in learning["topic"].lower() or query_lower in learning["content"].lower():
                relevant["learnings"].append(learning)
                if len(relevant["learnings"]) >= max_items:
                    break

        return relevant

    def save(self, project_name: str = "default"):
        """保存项目记忆到文件

        Args:
            project_name: 项目名称
        """
        file_path = os.path.join(self.storage_dir, f"{project_name}_memory.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(self.context, f, ensure_ascii=False, indent=2)

    def load(self, project_name: str = "default"):
        """从文件加载项目记忆

        Args:
            project_name: 项目名称
        """
        file_path = os.path.join(self.storage_dir, f"{project_name}_memory.json")
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                self.context = json.load(f)

    def to_prompt_context(self) -> str:
        """将记忆转换为Prompt上下文"""
        parts = []

        if self.context["decisions"]:
            parts.append("## 历史决策")
            for d in self.context["decisions"][-5:]:
                parts.append(f"- {d['decision']}: {d['reason']}")

        if self.context["patterns"]:
            parts.append("## 代码模式")
            for p in self.context["patterns"][-5:]:
                parts.append(f"- {p['name']}: {p['description']}")

        if self.context["errors"]:
            parts.append("## 已知问题及解决方案")
            for e in self.context["errors"][-5:]:
                parts.append(f"- {e['type']}: {e['solution']}")

        return "\n".join(parts) if parts else ""


class MemoryManager:
    """记忆管理器 - 统一管理对话和项目记忆"""

    def __init__(self, storage_dir: str = "data/memory"):
        self.conversations: Dict[str, ConversationMemory] = {}
        self.project_memory = ProjectMemory(storage_dir)
        self.storage_dir = storage_dir

    def get_conversation(self, agent_id: str) -> ConversationMemory:
        """获取或创建Agent的对话记忆

        Args:
            agent_id: Agent标识

        Returns:
            对话记忆实例
        """
        if agent_id not in self.conversations:
            self.conversations[agent_id] = ConversationMemory()
        return self.conversations[agent_id]

    def clear_conversation(self, agent_id: str):
        """清空Agent的对话历史

        Args:
            agent_id: Agent标识
        """
        if agent_id in self.conversations:
            self.conversations[agent_id].clear()

    def get_context_for_agent(self, agent_id: str, query: str = "") -> str:
        """获取Agent的完整上下文

        Args:
            agent_id: Agent标识
            query: 查询内容

        Returns:
            上下文字符串
        """
        parts = []

        project_ctx = self.project_memory.to_prompt_context()
        if project_ctx:
            parts.append(project_ctx)

        if query:
            relevant = self.project_memory.get_relevant_context(query)
            if any(relevant.values()):
                parts.append("## 相关经验")
                for category, items in relevant.items():
                    for item in items:
                        if category == "errors":
                            parts.append(f"- 问题: {item['type']} -> 解决: {item['solution']}")
                        elif category == "decisions":
                            parts.append(f"- 决策: {item['decision']}")

        return "\n\n".join(parts) if parts else ""
