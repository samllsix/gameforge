"""测试 Memory系统模块"""

import pytest
import json
import os
import tempfile
from src.core.memory import ConversationMemory, ProjectMemory, MemoryManager


class TestConversationMemory:
    """测试 ConversationMemory"""

    def test_init_default(self):
        """默认初始化"""
        mem = ConversationMemory()
        assert len(mem.messages) == 0
        assert mem.metadata == {}

    def test_init_custom_max(self):
        """自定义最大消息数"""
        mem = ConversationMemory(max_messages=10)
        assert mem.messages.maxlen == 10

    def test_add_message(self):
        """添加消息"""
        mem = ConversationMemory()
        mem.add_message("user", "hello")
        assert len(mem.messages) == 1
        assert mem.messages[0]["role"] == "user"
        assert mem.messages[0]["content"] == "hello"
        assert "timestamp" in mem.messages[0]

    def test_add_message_with_metadata(self):
        """添加带元数据的消息"""
        mem = ConversationMemory()
        mem.add_message("assistant", "response", {"model": "gpt-4"})
        assert mem.messages[0]["model"] == "gpt-4"

    def test_max_messages_limit(self):
        """消息数量限制"""
        mem = ConversationMemory(max_messages=3)
        for i in range(5):
            mem.add_message("user", f"msg{i}")
        assert len(mem.messages) == 3
        assert mem.messages[0]["content"] == "msg2"

    def test_get_messages_all(self):
        """获取所有消息"""
        mem = ConversationMemory()
        mem.add_message("user", "hello")
        mem.add_message("assistant", "hi")
        messages = mem.get_messages()
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"

    def test_get_messages_last_n(self):
        """获取最近N条消息"""
        mem = ConversationMemory()
        for i in range(5):
            mem.add_message("user", f"msg{i}")
        messages = mem.get_messages(last_n=2)
        assert len(messages) == 2
        assert messages[0]["content"] == "msg3"
        assert messages[1]["content"] == "msg4"

    def test_get_messages_excludes_metadata(self):
        """返回的消息不包含元数据"""
        mem = ConversationMemory()
        mem.add_message("user", "hello", {"extra": "data"})
        messages = mem.get_messages()
        assert "extra" not in messages[0]

    def test_get_context_window(self):
        """获取上下文窗口"""
        mem = ConversationMemory()
        mem.add_message("user", "hello")
        mem.add_message("assistant", "hi there")
        messages = mem.get_context_window(max_tokens=1000)
        assert len(messages) == 2

    def test_get_context_window_respects_limit(self):
        """上下文窗口尊重token限制"""
        mem = ConversationMemory()
        # 添加很长的消息
        long_msg = "x" * 10000  # ~2500 tokens
        mem.add_message("user", long_msg)
        mem.add_message("user", "short")
        messages = mem.get_context_window(max_tokens=100)
        assert len(messages) == 1
        assert messages[0]["content"] == "short"

    def test_clear(self):
        """清空对话历史"""
        mem = ConversationMemory()
        mem.add_message("user", "hello")
        mem.add_message("assistant", "hi")
        mem.clear()
        assert len(mem.messages) == 0

    def test_summary_empty(self):
        """空对话摘要"""
        mem = ConversationMemory()
        assert mem.summary() == "无对话历史"

    def test_summary_with_messages(self):
        """有消息的摘要"""
        mem = ConversationMemory()
        mem.add_message("user", "hello")
        mem.add_message("assistant", "hi")
        mem.add_message("user", "how are you?")
        summary = mem.summary()
        assert "3条消息" in summary
        assert "用户:2" in summary
        assert "助手:1" in summary


class TestProjectMemory:
    """测试 ProjectMemory"""

    def test_init(self, tmp_path):
        """初始化"""
        mem = ProjectMemory(storage_dir=str(tmp_path))
        assert "decisions" in mem.context
        assert "patterns" in mem.context
        assert "errors" in mem.context
        assert "learnings" in mem.context

    def test_add_decision(self, tmp_path):
        """添加决策"""
        mem = ProjectMemory(storage_dir=str(tmp_path))
        mem.add_decision("使用Unity", "跨平台支持好", "影响整个项目")
        assert len(mem.context["decisions"]) == 1
        assert mem.context["decisions"][0]["decision"] == "使用Unity"

    def test_add_pattern(self, tmp_path):
        """添加模式"""
        mem = ProjectMemory(storage_dir=str(tmp_path))
        mem.add_pattern("单例模式", "全局唯一实例", "public static Instance;")
        assert len(mem.context["patterns"]) == 1
        assert mem.context["patterns"][0]["name"] == "单例模式"

    def test_add_error(self, tmp_path):
        """添加错误记录"""
        mem = ProjectMemory(storage_dir=str(tmp_path))
        mem.add_error("NullReferenceException", "检查空引用", "PlayerController")
        assert len(mem.context["errors"]) == 1
        assert mem.context["errors"][0]["type"] == "NullReferenceException"

    def test_add_learning(self, tmp_path):
        """添加学习记录"""
        mem = ProjectMemory(storage_dir=str(tmp_path))
        mem.add_learning("Unity物理", "使用Rigidbody2D")
        assert len(mem.context["learnings"]) == 1
        assert mem.context["learnings"][0]["topic"] == "Unity物理"

    def test_get_relevant_context(self, tmp_path):
        """获取相关上下文"""
        mem = ProjectMemory(storage_dir=str(tmp_path))
        mem.add_decision("使用Unity引擎", "跨平台支持好")
        mem.add_pattern("Unity单例", "MonoBehaviour单例")
        mem.add_error("Unity编译错误", "检查脚本依赖")

        relevant = mem.get_relevant_context("Unity")
        assert len(relevant["decisions"]) == 1
        assert len(relevant["patterns"]) == 1
        assert len(relevant["errors"]) == 1

    def test_get_relevant_context_max_items(self, tmp_path):
        """相关上下文数量限制"""
        mem = ProjectMemory(storage_dir=str(tmp_path))
        for i in range(10):
            mem.add_decision(f"决策{i}", "原因")

        relevant = mem.get_relevant_context("决策", max_items=3)
        assert len(relevant["decisions"]) == 3

    def test_save_and_load(self, tmp_path):
        """保存和加载"""
        mem = ProjectMemory(storage_dir=str(tmp_path))
        mem.add_decision("测试决策", "测试原因")
        mem.save("test_project")

        # 验证文件存在
        file_path = tmp_path / "test_project_memory.json"
        assert file_path.exists()

        # 加载到新实例
        mem2 = ProjectMemory(storage_dir=str(tmp_path))
        mem2.load("test_project")
        assert len(mem2.context["decisions"]) == 1
        assert mem2.context["decisions"][0]["decision"] == "测试决策"

    def test_load_nonexistent(self, tmp_path):
        """加载不存在的文件"""
        mem = ProjectMemory(storage_dir=str(tmp_path))
        original_context = mem.context.copy()
        mem.load("nonexistent")
        assert mem.context == original_context

    def test_to_prompt_context_empty(self, tmp_path):
        """空记忆的prompt上下文"""
        mem = ProjectMemory(storage_dir=str(tmp_path))
        assert mem.to_prompt_context() == ""

    def test_to_prompt_context_with_content(self, tmp_path):
        """有内容的prompt上下文"""
        mem = ProjectMemory(storage_dir=str(tmp_path))
        mem.add_decision("使用Unity", "跨平台")
        mem.add_pattern("单例", "全局唯一")
        mem.add_error("NPE", "检查空值")

        context = mem.to_prompt_context()
        assert "历史决策" in context
        assert "代码模式" in context
        assert "已知问题" in context
        assert "使用Unity" in context


class TestMemoryManager:
    """测试 MemoryManager"""

    def test_init(self, tmp_path):
        """初始化"""
        manager = MemoryManager(storage_dir=str(tmp_path))
        assert manager.conversations == {}
        assert isinstance(manager.project_memory, ProjectMemory)

    def test_get_conversation_creates(self, tmp_path):
        """获取对话记忆会创建"""
        manager = MemoryManager(storage_dir=str(tmp_path))
        conv = manager.get_conversation("agent1")
        assert isinstance(conv, ConversationMemory)
        assert "agent1" in manager.conversations

    def test_get_conversation_returns_same(self, tmp_path):
        """相同agent返回同一实例"""
        manager = MemoryManager(storage_dir=str(tmp_path))
        conv1 = manager.get_conversation("agent1")
        conv2 = manager.get_conversation("agent1")
        assert conv1 is conv2

    def test_clear_conversation(self, tmp_path):
        """清空对话历史"""
        manager = MemoryManager(storage_dir=str(tmp_path))
        conv = manager.get_conversation("agent1")
        conv.add_message("user", "hello")
        manager.clear_conversation("agent1")
        assert len(conv.messages) == 0

    def test_clear_nonexistent_conversation(self, tmp_path):
        """清空不存在的对话（不报错）"""
        manager = MemoryManager(storage_dir=str(tmp_path))
        manager.clear_conversation("nonexistent")

    def test_get_context_for_agent_empty(self, tmp_path):
        """无上下文时返回空"""
        manager = MemoryManager(storage_dir=str(tmp_path))
        context = manager.get_context_for_agent("agent1")
        assert context == ""

    def test_get_context_for_agent_with_project(self, tmp_path):
        """有项目上下文"""
        manager = MemoryManager(storage_dir=str(tmp_path))
        manager.project_memory.add_decision("测试决策", "测试原因")
        context = manager.get_context_for_agent("agent1")
        assert "历史决策" in context
        assert "测试决策" in context

    def test_get_context_for_agent_with_query(self, tmp_path):
        """带查询的上下文"""
        manager = MemoryManager(storage_dir=str(tmp_path))
        manager.project_memory.add_error("Unity错误", "修复方案")
        context = manager.get_context_for_agent("agent1", query="Unity")
        assert "相关经验" in context
        assert "Unity错误" in context
