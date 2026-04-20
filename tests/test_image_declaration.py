# -*- coding: utf-8 -*-
"""图片声明注入测试"""

import pytest
from tutu_core.seedance_client import (
    build_image_declaration, inject_image_declaration, match_expressions,
)


class TestBuildImageDeclaration:
    def test_no_expressions(self):
        decl, matched = build_image_declaration("秃秃在家待着，什么表情都没有")
        assert "图片1是小蘑菇角色" in decl
        assert "图片2是肢体末端" in decl
        assert "图片3是张嘴表情" in decl
        assert "图片4是全身比例" in decl
        assert "图片5" not in decl
        assert matched == []

    def test_with_happy(self):
        decl, matched = build_image_declaration("秃秃眯眼笑，腮帮子鼓")
        assert "图片5" in decl
        assert "开心" in decl
        assert "happy" in matched

    def test_with_multiple(self):
        decl, matched = build_image_declaration("先开心，然后委屈地哭了，眼泪流下来")
        assert "图片5" in decl
        assert "图片6" in decl
        assert "happy" in matched
        assert "cry" in matched


class TestInjectImageDeclaration:
    def test_replaces_single_image_line(self):
        orig = "图片1是小蘑菇角色形象参考。微缩场景..."
        injected = inject_image_declaration(orig)
        assert "图片1是小蘑菇角色" in injected
        assert "图片2是肢体末端" in injected
        assert "微缩场景" in injected  # 保留原内容
        assert injected.count("图片1是") == 1  # 不重复

    def test_idempotent(self):
        """已含 图片2 的 prompt 不应被重复注入"""
        orig = "图片1是角色。图片2是肢体。0-3s..."
        result = inject_image_declaration(orig)
        assert result == orig  # 未改

    def test_no_image_prefix_fallback(self):
        orig = "某个不以图片1开头的 prompt"
        result = inject_image_declaration(orig)
        assert result.startswith("图片1是")


class TestMatchExpressionsAlreadyCovered:
    """已在 test_seedance_client.py 有覆盖，这里只验证与 build_image_declaration 协同"""
    def test_coherent(self):
        text = "脸红害羞地躲进帽子"
        matched1 = match_expressions(text)
        _, matched2 = build_image_declaration(text)
        assert matched1 == matched2
