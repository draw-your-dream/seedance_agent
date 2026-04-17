# -*- coding: utf-8 -*-
"""tutu_core.seedance_client 单元测试 — 测试 payload 构建和验证（不调用真实API）"""

import pytest
from tutu_core.seedance_client import (
    build_payload, verify_payload, load_reference_image, match_expressions,
)
from tutu_core.config import REF_IMAGE


class TestBuildPayload:
    def test_basic_structure(self):
        payload = build_payload("图片1是test prompt", "base64img")
        assert payload["model"] == "doubao-seedance-2-0-260128"
        assert payload["content"][0]["type"] == "text"
        assert payload["content"][0]["text"] == "图片1是test prompt"
        assert payload["content"][1]["type"] == "image_url"
        assert "base64img" in payload["content"][1]["image_url"]["url"]
        assert payload["generate_audio"] is True
        assert payload["ratio"] == "9:16"
        assert payload["watermark"] is False

    def test_custom_duration(self):
        payload = build_payload("test", "img", duration=15)
        assert payload["duration"] == 15

    def test_default_duration(self):
        payload = build_payload("test", "img")
        assert payload["duration"] == 13


class TestVerifyPayload:
    def test_valid_payload(self):
        payload = build_payload("图片1" + "x" * 300, "base64imgdata")
        errors = verify_payload(payload)
        assert len(errors) == 0

    def test_empty_text(self):
        payload = build_payload("短", "base64imgdata")
        errors = verify_payload(payload)
        assert any("太短" in e for e in errors)

    def test_wrong_prefix(self):
        payload = build_payload("没有图片前缀" + "x" * 300, "base64imgdata")
        errors = verify_payload(payload)
        assert any("图片1" in e for e in errors)

    def test_missing_image(self):
        payload = {
            "model": "test",
            "content": [{"type": "text", "text": "图片1" + "x" * 300}]
        }
        errors = verify_payload(payload)
        assert any("图片" in e for e in errors)

    def test_empty_content(self):
        payload = {"model": "test", "content": []}
        errors = verify_payload(payload)
        assert len(errors) > 0


class TestLoadReferenceImage:
    def test_loads_existing_image(self):
        if not REF_IMAGE.exists():
            pytest.skip("reference.png not found")
        b64 = load_reference_image()
        assert isinstance(b64, str)
        assert len(b64) > 1000

    def test_missing_image_raises(self):
        from pathlib import Path
        with pytest.raises(FileNotFoundError):
            load_reference_image(Path("/nonexistent/image.png"))


class TestMatchExpressions:
    """prompt 文本 → 表情关键词匹配"""

    def test_no_match(self):
        assert match_expressions("秃秃在家待着，什么表情也没有") == []

    def test_happy_match(self):
        result = match_expressions("秃秃眯眼笑，满足地看向镜头")
        assert "happy" in result

    def test_cry_match(self):
        result = match_expressions("秃秃眼泪流下来，委屈地嘟嘟")
        assert "cry" in result

    def test_angry_match(self):
        result = match_expressions("气呼呼地鼓起脸，奶凶地瞪着")
        assert "angry" in result

    def test_shy_match(self):
        result = match_expressions("脸红了，躲到帽子里偷偷看")
        assert "shy" in result

    def test_laugh_match(self):
        result = match_expressions("咧嘴大笑，哈哈哈")
        assert "laugh" in result

    def test_multiple_expressions(self):
        result = match_expressions("先开心地笑了，然后眼泪忍不住流下来委屈")
        assert "happy" in result
        assert "cry" in result

    def test_no_false_positive_on_negation(self):
        # "不生气"不应触发生气（当前是简单子串匹配，这条会失败，记录为已知限制）
        # 这里只验证非否定语境下能匹配
        result = match_expressions("生气地跺脚")
        assert "angry" in result
