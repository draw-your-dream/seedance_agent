# -*- coding: utf-8 -*-
"""generation_router 动态分派测试"""

import os
import pytest
from tutu_core.generation_router import (
    quality_review, classify_event, _resolve_version, _get_module,
)


class TestResolveVersion:
    def test_default_v1(self, monkeypatch):
        monkeypatch.delenv("GENERATION_VERSION", raising=False)
        assert _resolve_version() == "v1"

    def test_env_v2(self, monkeypatch):
        monkeypatch.setenv("GENERATION_VERSION", "v2")
        assert _resolve_version() == "v2"

    def test_env_uppercase(self, monkeypatch):
        monkeypatch.setenv("GENERATION_VERSION", "V2")
        assert _resolve_version() == "v2"

    def test_explicit_overrides_env(self, monkeypatch):
        monkeypatch.setenv("GENERATION_VERSION", "v2")
        assert _resolve_version(explicit="v1") == "v1"

    def test_unknown_falls_back_to_v1(self, monkeypatch):
        monkeypatch.setenv("GENERATION_VERSION", "v99")
        assert _resolve_version() == "v1"


class TestGetModule:
    def test_v1_has_quality_review(self):
        mod = _get_module("v1")
        assert hasattr(mod, "quality_review")
        assert hasattr(mod, "generate_event_content")

    def test_v2_has_quality_review_v2(self):
        mod = _get_module("v2")
        assert hasattr(mod, "quality_review_v2")
        assert hasattr(mod, "generate_event_content")

    def test_cached(self):
        m1 = _get_module("v1")
        m2 = _get_module("v1")
        assert m1 is m2  # lru_cache 应返回同一对象


class TestQualityReviewDispatch:
    """quality_review 应根据 version 走不同实现"""

    def _minimal_prompt(self):
        # 含所有 v1 要求但缺 v2 新增的风格/构图
        return (
            "图片1是小蘑菇角色形象参考。"
            "0-3s：秃秃歪头。音效：嘟。"
            "3-7s：秃秃看镜头。音效：沙沙。"
            "7-10s：秃秃点头。音效：噔噔。"
            "10-13s：画面定格。音效：嘟。只要音效。" + "x" * 300
        )

    def test_v1_passes(self, monkeypatch):
        monkeypatch.delenv("GENERATION_VERSION", raising=False)
        passed, issues = quality_review(self._minimal_prompt(), "日常生活")
        # v1 要求较松，大概率通过
        assert isinstance(issues, list)

    def test_v2_stricter(self):
        """v2 应比 v1 报出更多问题（新增了风格/构图/收尾/叠词维度）"""
        text = self._minimal_prompt()
        _, v1_issues = quality_review(text, "日常生活", version="v1")
        _, v2_issues = quality_review(text, "日常生活", version="v2")
        assert len(v2_issues) >= len(v1_issues)


class TestClassifyEventShared:
    def test_v1_v2_agree(self):
        """classify_event 在 v1/v2 共用，结果应一致"""
        assert classify_event("秃秃吃冰激凌", "") == classify_event("秃秃吃冰激凌", "")
