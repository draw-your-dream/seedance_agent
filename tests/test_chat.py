# -*- coding: utf-8 -*-
"""chat.py 单元测试"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# 添加项目路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))


class TestChatReply:
    """chat_reply 函数测试"""

    def _mock_db(self):
        """构造 mock 的 database 模块"""
        mock_db = MagicMock()
        mock_db.get_today_events.return_value = [
            {"title": "秃秃赖床", "time": "08:00", "triggered_by": "daily", "publish_at": "2026-04-11T08:00:00"}
        ]
        mock_db.get_chat_history.return_value = [
            {"sender": "user", "content": "你好"},
            {"sender": "tutu", "content": "嘟？"},
        ]
        return mock_db

    @patch("chat.db")
    @patch("chat.call_llm")
    def test_normal_json_response(self, mock_llm, mock_db_module):
        """LLM 返回正常 JSON 时应正确解析"""
        import chat

        mock_db_module.get_today_events.return_value = []
        mock_db_module.get_chat_history.return_value = []
        mock_llm.return_value = '```json\n{"reply": "嘟嘟，你好呀", "trigger_video": false, "video_desc": ""}\n```'

        result = chat.chat_reply("你好")
        assert result["reply"] == "嘟嘟，你好呀"
        assert result["trigger_video"] is False
        assert result["video_desc"] == ""

    @patch("chat.db")
    @patch("chat.call_llm")
    def test_trigger_video(self, mock_llm, mock_db_module):
        """有趣内容应触发视频"""
        import chat

        mock_db_module.get_today_events.return_value = []
        mock_db_module.get_chat_history.return_value = []
        mock_llm.return_value = '{"reply": "好漂亮的花！", "trigger_video": true, "video_desc": "秃秃去阳台看花"}'

        result = chat.chat_reply("我拍了一朵花", image_desc="一朵红色玫瑰")
        assert result["trigger_video"] is True
        assert "看花" in result["video_desc"]

    @patch("chat.db")
    @patch("chat.call_llm")
    def test_trigger_limit(self, mock_llm, mock_db_module):
        """超过每日上限时不触发视频"""
        import chat

        mock_db_module.get_today_events.return_value = [
            {"triggered_by": "user", "title": "a", "time": "10:00", "publish_at": "2026-04-11T10:00:00"},
            {"triggered_by": "user", "title": "b", "time": "11:00", "publish_at": "2026-04-11T11:00:00"},
        ]
        mock_db_module.get_chat_history.return_value = []
        mock_llm.return_value = '{"reply": "好漂亮", "trigger_video": true, "video_desc": "去看花"}'

        result = chat.chat_reply("又拍了一朵花")
        assert result["trigger_video"] is False
        assert result["video_desc"] == ""

    @patch("chat.db")
    @patch("chat.call_llm")
    def test_llm_returns_none(self, mock_llm, mock_db_module):
        """LLM 返回 None 时的 fallback"""
        import chat

        mock_db_module.get_today_events.return_value = []
        mock_db_module.get_chat_history.return_value = []
        mock_llm.return_value = None

        result = chat.chat_reply("你好")
        assert result["reply"] == "嘟？"
        assert result["trigger_video"] is False

    @patch("chat.db")
    @patch("chat.call_llm")
    def test_llm_returns_invalid_json(self, mock_llm, mock_db_module):
        """LLM 返回无法解析的文本时的 fallback"""
        import chat

        mock_db_module.get_today_events.return_value = []
        mock_db_module.get_chat_history.return_value = []
        mock_llm.return_value = "嗯……让我想想，你说的那个事情嘛，秃秃觉得很有意思呢"

        result = chat.chat_reply("你好")
        assert len(result["reply"]) > 0
        assert result["trigger_video"] is False
        # 确保不包含 markdown 残留
        assert "```" not in result["reply"]

    @patch("chat.db")
    @patch("chat.call_llm")
    def test_system_prompt_separation(self, mock_llm, mock_db_module):
        """确认 system prompt 和 user prompt 分离调用"""
        import chat

        mock_db_module.get_today_events.return_value = []
        mock_db_module.get_chat_history.return_value = []
        mock_llm.return_value = '{"reply": "嘟", "trigger_video": false, "video_desc": ""}'

        chat.chat_reply("你好")

        # 验证 call_llm 被调用时 system_prompt 非空
        args = mock_llm.call_args
        system_prompt = args[0][0] if args[0] else args[1].get("system_prompt", "")
        assert len(system_prompt) > 0, "system_prompt should not be empty"


class TestPersonalityCache:
    """personality 缓存测试"""

    def test_cache_returns_same_content(self, tmp_path):
        """相同 mtime 应命中缓存"""
        import chat

        p = tmp_path / "personality.md"
        p.write_text("测试人格", encoding="utf-8")

        with patch.object(chat, "PERSONALITY_FILE", p):
            # 重置缓存
            chat._personality_cache.update({"text": None, "mtime": 0})
            r1 = chat._load_personality()
            r2 = chat._load_personality()
            assert r1 == r2 == "测试人格"

    def test_cache_refreshes_on_change(self, tmp_path):
        """文件修改后应刷新缓存"""
        import time
        import chat

        p = tmp_path / "personality.md"
        p.write_text("版本1", encoding="utf-8")

        with patch.object(chat, "PERSONALITY_FILE", p):
            chat._personality_cache.update({"text": None, "mtime": 0})
            r1 = chat._load_personality()
            assert r1 == "版本1"

            # 模拟文件变更（需要不同 mtime）
            time.sleep(0.05)
            p.write_text("版本2", encoding="utf-8")
            r2 = chat._load_personality()
            assert r2 == "版本2"
