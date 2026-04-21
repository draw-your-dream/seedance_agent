# -*- coding: utf-8 -*-
"""tutu_core.validators 单元测试"""

import pytest
from tutu_core.validators import validate_prompt, quick_validate
from tutu_core.generation import quality_review


# ============================================================
# validate_prompt
# ============================================================

def _make_prompt(extra=""):
    """构造一个最小合法prompt（匹配新的 REQUIRED_SUFFIXES: 黑色/小肉球/没有尾巴）"""
    base = (
        "图片1是小蘑菇角色形象参考。微缩场景。"
        "0-3s：蘑菇角色蜷缩。音效：嘟。"
        "3-7s：蘑菇角色跳跃。音效：啪。"
        "7-10s：蘑菇角色奔跑。音效：沙沙。"
        "10-13s：蘑菇角色看镜头。画面定格。"
        "只要音效。角色特征：嘴巴里是黑色的，手是小肉球形状，没有尾巴。"
    )
    padded = base + "x" * max(0, 300 - len(base)) + extra
    return padded


class TestValidatePrompt:
    def test_valid_prompt_passes(self):
        passed, issues = validate_prompt(_make_prompt())
        assert passed is True
        errors = [i for i in issues if i.startswith("❌")]
        assert len(errors) == 0

    def test_missing_prefix(self):
        text = "没有图片开头" + "x" * 300 + "0-3s 音效 黑色 小肉球 没有尾巴 镜头"
        passed, issues = validate_prompt(text)
        assert passed is False
        assert any("图片1" in i for i in issues)

    def test_missing_suffix(self):
        # 移除必须的关键词"小肉球"
        text = _make_prompt().replace("小肉球", "")
        passed, issues = validate_prompt(text)
        assert passed is False
        assert any("小肉球" in i for i in issues)

    def test_too_short(self):
        text = "图片1 0-3s 音效 黑色 小肉球 没有尾巴 镜头"
        passed, issues = validate_prompt(text)
        assert passed is False
        assert any("太短" in i for i in issues)

    def test_too_long_is_warning_not_error(self):
        text = _make_prompt("长" * 700)
        passed, issues = validate_prompt(text)
        # 太长是警告⚠️，不是错误❌
        assert passed is True
        assert any("偏长" in i for i in issues)

    def test_missing_timecode(self):
        text = "图片1test " + "x" * 300 + " 音效 黑色 小肉球 没有尾巴 镜头定格"
        passed, issues = validate_prompt(text)
        assert passed is False
        assert any("时间码" in i for i in issues)

    def test_missing_sound(self):
        text = (
            "图片1是小蘑菇。0-3s：蘑菇跳。3-7s：走。7-10s：跑。"
            "10-13s：看镜头。画面定格。角色特征：嘴巴黑色，手是小肉球，没有尾巴。"
            + "x" * 300
        )
        passed, issues = validate_prompt(text)
        assert passed is False
        assert any("音效" in i for i in issues)

    def test_forbidden_word_in_positive_sentence(self):
        text = _make_prompt("\n蘑菇角色露出牙齿笑了。")
        passed, issues = validate_prompt(text)
        assert passed is False
        assert any("禁止词" in i and "牙齿" in i for i in issues)

    def test_forbidden_word_in_negation_is_ok(self):
        """'没有牙齿' 不应该触发禁止词"""
        text = _make_prompt("\n角色没有牙齿和舌头。")
        passed, issues = validate_prompt(text)
        forbidden_hits = [i for i in issues if "禁止词" in i and "牙齿" in i]
        assert len(forbidden_hits) == 0

    def test_aggressive_word(self):
        text = _make_prompt("\n蘑菇角色暴怒了。")
        passed, issues = validate_prompt(text)
        assert passed is False
        assert any("暴怒" in i for i in issues)

    def test_missing_interaction_beat_is_warning(self):
        text = (
            "图片1是小蘑菇。微缩。0-3s：走路。音效：嘟。"
            "3-7s：跳。音效：啪。7-10s：看。音效：沙。"
            "10-13s：结束了就这样吧。音效：无。"
            "嘴巴黑色、小肉球的手、没有尾巴。" + "x" * 200
        )
        passed, issues = validate_prompt(text)
        # 缺互动beat 只是⚠️警告
        warning_hits = [i for i in issues if "互动beat" in i]
        assert len(warning_hits) > 0
        assert all(i.startswith("⚠️") for i in warning_hits)


# ============================================================
# quick_validate
# ============================================================

class TestQuickValidate:
    def test_good_prompt(self):
        text = "图片1 0-3s 音效 嘟 构图 微缩 不要太大"
        result = quick_validate(text)
        assert result["passed"] is True
        assert result["score"] == 5

    def test_missing_everything(self):
        result = quick_validate("这是一段普通文本。")
        assert result["passed"] is False
        assert result["score"] < 5

    def test_forbidden_word_detected(self):
        result = quick_validate("角色露出手指 0-3s 音效 构图 微缩")
        assert result["passed"] is False
        assert any("手指" in i for i in result["issues"])

    def test_forbidden_word_in_negation_ignored(self):
        result = quick_validate("不出现手指 0-3s 音效 构图 微缩 不要太大")
        forbidden = [i for i in result["issues"] if "手指" in i]
        assert len(forbidden) == 0


# ============================================================
# 禁止说话（quality_review 中的新增检查）
# ============================================================

class TestNoSpeech:
    """秃秃只能用拟声词，不能有台词/独白"""

    def _base(self) -> str:
        """一段合格的 v1 prompt 基座（含所有必需要素），用于拼接违规片段测试"""
        return (
            "图片1是小蘑菇角色形象参考。中近景对称构图。不要太大。"
            "0-3秒：秃秃歪头看镜头，眼睛眯起来，腮帮子鼓鼓的。音效：嘟嘟。"
            "3-7秒：它慢慢爬，沙沙声。表情：脸红。"
            "7-10秒：duang 一声弹起。音效：啪叽。"
            "10-13秒：望向镜头定格。音效：嘟～。"
            + "x" * 200
        )

    def test_clean_passes(self):
        passed, issues = quality_review(self._base(), "日常生活")
        assert not any("说话" in i or "台词" in i for i in issues)

    def test_rejects_inner_monologue(self):
        text = self._base() + '秃秃仿佛在无声地说："看，我搞定了。"'
        _, issues = quality_review(text, "日常生活")
        assert any("说话" in i or "台词" in i for i in issues)

    def test_rejects_speech_quote(self):
        text = self._base() + '秃秃高兴地说："今天真开心呢！"'
        _, issues = quality_review(text, "日常生活")
        assert any("说话" in i or "台词" in i for i in issues)

    def test_rejects_like_said(self):
        text = self._base() + '像在说："你好呀朋友"，然后眨眼。'
        _, issues = quality_review(text, "日常生活")
        assert any("说话" in i or "台词" in i for i in issues)

    def test_onomatopoeia_quote_ok(self):
        """拟声词用引号包围是允许的"""
        text = self._base() + '发出一声满足的"嘟～"。'
        _, issues = quality_review(text, "日常生活")
        assert not any("说话" in i or "台词" in i for i in issues)

    def test_short_quote_ok(self):
        """极短的引号（如拟声）不触发，至少 3 字才算人类语句"""
        text = self._base() + '嘟！"啊"一下。'
        _, issues = quality_review(text, "日常生活")
        assert not any("说话" in i or "台词" in i for i in issues)
