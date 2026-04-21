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
        assert "图片4是屁股特写" in decl
        assert "图片5是全身比例" in decl
        assert "图片6" not in decl
        assert matched == []

    def test_with_happy(self):
        decl, matched = build_image_declaration("秃秃眯眼笑，腮帮子鼓")
        assert "图片6" in decl
        assert "开心" in decl
        assert "happy" in matched

    def test_with_multiple(self):
        decl, matched = build_image_declaration("先开心，然后委屈地哭了，眼泪流下来")
        assert "图片6" in decl
        assert "图片7" in decl
        assert "happy" in matched
        assert "cry" in matched


class TestInjectImageDeclaration:
    """现在的语义：永远剥离 LLM 的图片声明，规则重新贴上（含表情图）"""

    def test_replaces_single_image_line(self):
        """LLM 只写了图片1 → 规则补足 2/3/4/5"""
        orig = "图片1是小蘑菇角色形象参考。微缩场景..."
        injected = inject_image_declaration(orig)
        assert "图片2是肢体末端" in injected
        assert "图片3是张嘴" in injected
        assert "图片4是屁股" in injected
        assert "图片5是全身" in injected
        assert "微缩场景" in injected  # 正文保留

    def test_llm_missed_expression_gets_added(self):
        """关键 case：LLM 自己写了 1-5 号但漏了表情图，规则补上"""
        # LLM 输出看起来已经"完整"，但其实缺少 happy 表情图（因为内容里有"开心"）
        orig = (
            "图片1是小蘑菇角色形象参考。图片2是肢体末端特写参考。"
            "图片3是张嘴特写参考。图片4是屁股特写参考。图片5是全身比例参考。"
            "0-3s：秃秃开心地眯眼笑..."
        )
        injected = inject_image_declaration(orig)
        # 必须出现表情图 6
        assert "图片6" in injected
        assert "开心" in injected
        # LLM 写的 1-5 声明不能重复（只保留规则那一份）
        assert injected.count("图片1是") == 1
        assert injected.count("图片2是") == 1

    def test_no_image_prefix_fallback(self):
        orig = "某个不以图片开头的 prompt"
        result = inject_image_declaration(orig)
        assert result.startswith("图片1是")
        assert "某个不以图片开头的 prompt" in result


class TestPlaceholderResolution:
    """占位符 → 图片编号替换"""

    def test_placeholder_triggers_match(self):
        """{happy} 占位符应被当作匹配信号"""
        from tutu_core.seedance_client import match_expressions
        m = match_expressions("秃秃蹲在桌边（参考{happy}表情图）")
        assert "happy" in m

    def test_keyword_and_placeholder_union(self):
        """关键词 + 占位符 应取并集"""
        from tutu_core.seedance_client import match_expressions
        m = match_expressions("秃秃开心地笑（参考{cry}情绪图）")
        assert "happy" in m  # 从"开心"关键词
        assert "cry" in m  # 从占位符

    def test_resolve_single(self):
        """单个占位符 → 图片6"""
        from tutu_core.seedance_client import resolve_expression_placeholders
        text = "参考{happy}表情图的眯眼"
        out = resolve_expression_placeholders(text, ["happy"])
        assert "{happy}" not in out
        assert "图片6" in out

    def test_resolve_two_in_order(self):
        """两个占位符按 matched 顺序分配 图片6/图片7"""
        from tutu_core.seedance_client import resolve_expression_placeholders
        text = "先开心（{happy}情绪图），然后哭了（{cry}情绪图）"
        out = resolve_expression_placeholders(text, ["happy", "cry"])
        # happy 是 list 第0个 → 图片6，cry 是第1个 → 图片7
        assert "图片6" in out
        assert "图片7" in out
        assert "{happy}" not in out
        assert "{cry}" not in out

    def test_resolve_various_suffixes(self):
        """多种后缀变体都要能替换"""
        from tutu_core.seedance_client import resolve_expression_placeholders
        text = "A {happy}情绪图片 B {happy}表情图片 C {happy}情绪图 D {happy}表情图 E {happy}"
        out = resolve_expression_placeholders(text, ["happy"])
        assert "{happy}" not in out
        assert out.count("图片6") == 5


class TestMatchExpressionsAlreadyCovered:
    """已在 test_seedance_client.py 有覆盖，这里只验证与 build_image_declaration 协同"""
    def test_coherent(self):
        text = "脸红害羞地躲进帽子"
        matched1 = match_expressions(text)
        _, matched2 = build_image_declaration(text)
        assert matched1 == matched2
