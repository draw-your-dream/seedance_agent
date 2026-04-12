# -*- coding: utf-8 -*-
"""tutu_core.llm_client 单元测试 — 主要测 extract_json 和缓存"""

import json
import pytest
from tutu_core.llm_client import extract_json, clear_cache, _cache, _cache_key


# ============================================================
# extract_json
# ============================================================

class TestExtractJson:
    def test_json_code_block(self):
        text = '一些文字\n```json\n{"a": 1, "b": "hello"}\n```\n更多文字'
        assert extract_json(text) == {"a": 1, "b": "hello"}

    def test_generic_code_block(self):
        text = '文字\n```\n{"x": [1,2,3]}\n```'
        assert extract_json(text) == {"x": [1, 2, 3]}

    def test_bare_json_object(self):
        text = '这是LLM输出 {"key": "value"} 结尾'
        assert extract_json(text) == {"key": "value"}

    def test_bare_json_array(self):
        text = '前缀 [{"id": 1}, {"id": 2}] 后缀'
        result = extract_json(text, expect_array=True)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_nested_braces(self):
        text = '{"outer": {"inner": {"deep": 1}}, "b": 2}'
        result = extract_json(text)
        assert result["outer"]["inner"]["deep"] == 1
        assert result["b"] == 2

    def test_string_with_braces(self):
        """JSON字符串内的花括号不应干扰匹配"""
        text = '{"text": "hello {world} end"}'
        result = extract_json(text)
        assert result["text"] == "hello {world} end"

    def test_escaped_quotes(self):
        text = r'{"text": "say \"hello\""}'
        result = extract_json(text)
        assert 'hello' in result["text"]

    def test_multiple_code_blocks_first_wins(self):
        text = '```json\n{"first": true}\n```\n\n```json\n{"second": true}\n```'
        result = extract_json(text)
        assert result["first"] is True

    def test_array_extraction(self):
        text = '日程如下：\n[{"time": "08:00", "title": "起床"}]'
        result = extract_json(text, expect_array=True)
        assert result[0]["time"] == "08:00"

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="无法从LLM输出中提取JSON"):
            extract_json("这里没有任何JSON")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_chinese_content(self):
        text = '```json\n{"title": "秃秃吃提拉米苏", "嘟": true}\n```'
        result = extract_json(text)
        assert result["title"] == "秃秃吃提拉米苏"

    def test_code_block_with_language_tag(self):
        text = '```javascript\n{"not_this": true}\n```'
        # 通用代码块尝试解析
        result = extract_json(text)
        assert result["not_this"] is True

    def test_json_with_trailing_comma_fails_gracefully(self):
        """非法JSON应该尝试下一个策略或报错"""
        text = '```json\n{"a": 1,}\n```\n后面有 {"a": 1}'
        # 代码块里是非法JSON，但后面有合法的裸JSON
        result = extract_json(text)
        assert result["a"] == 1


# ============================================================
# 缓存机制
# ============================================================

class TestCache:
    def test_cache_key_deterministic(self):
        k1 = _cache_key("sys", "user", "gemini")
        k2 = _cache_key("sys", "user", "gemini")
        assert k1 == k2

    def test_cache_key_differs_by_provider(self):
        k1 = _cache_key("sys", "user", "gemini")
        k2 = _cache_key("sys", "user", "claude")
        assert k1 != k2

    def test_clear_cache(self):
        _cache["test_key"] = "test_value"
        clear_cache()
        assert len(_cache) == 0
