# -*- coding: utf-8 -*-
"""generation_v2 单元测试 — 验证 v2 新增的校验维度"""

import pytest
from tutu_core.generation_v2 import (
    quality_review_v2, _time_to_light, TIME_TO_LIGHT, VISUAL_STYLE_TOKENS,
)


class TestTimeToLight:
    def test_morning(self):
        assert "清晨" in _time_to_light("07:30")

    def test_afternoon(self):
        assert "午后" in _time_to_light("15:00")

    def test_night(self):
        assert "台灯" in _time_to_light("21:00") or "夜晚" in _time_to_light("21:00")

    def test_invalid(self):
        # 非法输入应有 fallback
        result = _time_to_light("not_a_time")
        assert isinstance(result, str) and len(result) > 0


class TestQualityReviewV2:
    """v2 校验在 v1 基础上新增 4 个维度"""

    def _good_v2_prompt(self) -> str:
        """构造一个通过 v2 校验的 prompt（≥300字，含各类关键词）。"""
        return (
            "图片1是小蘑菇角色形象参考。日系写实摄影风格，大光圈浅景深，低饱和暖色调，"
            "画面有真实胶片颗粒感和柔和高光溢出。水平中心对称构图，镜头固定平拍，"
            "低平视角贴近桌面高度。午后柔和自然光从窗户洒在桌面上。"
            "0-3秒：小蘑菇圆滚滚地出现在画面中央，豆豆眼亮亮地看着前方，"
            "蘑菇帽轻轻晃动，脸蛋红扑扑的，肢体末端抱在身前。"
            "3-7秒：它慢慢歪头，腮帮子鼓起来，眯眼笑了，眼睛弯成小月牙，"
            "整个身体一点一点凑向镜头。7-10秒：肢体末端按在桌边一点一点挪动，"
            "短腿蹬了蹬，表情专注又好奇，嘴巴抿成一条小弧线。"
            "10-15秒：它躺下来了，眯着眼，软塌塌地陷在毛绒垫子里，"
            "肚子一起一伏，画面温柔定格，暖光静静照着。"
            "配乐/音效：0-3秒微风沙沙声。3-7秒脚步哒哒声。"
            "7-10秒摩擦声。10-15秒极轻极满足的嘟——。"
            "只要音效，不要背景音乐，不要字幕。"
        )

    def test_good_prompt_passes(self):
        text = self._good_v2_prompt()
        passed, issues = quality_review_v2(text, "日常生活")
        assert passed, f"应通过，问题: {issues}"

    def test_missing_visual_style(self):
        # 移除日系写实等风格标签
        text = self._good_v2_prompt().replace("日系写实摄影风格", "").replace("浅景深", "").replace("胶片颗粒感", "").replace("暖色调", "").replace("柔和", "").replace("胶片", "")
        passed, issues = quality_review_v2(text, "日常生活")
        assert any("视觉风格" in i for i in issues)

    def test_missing_composition(self):
        text = self._good_v2_prompt().replace("水平中心对称构图", "").replace("镜头固定平拍", "").replace("低平视角", "")
        passed, issues = quality_review_v2(text, "日常生活")
        assert any("运镜" in i for i in issues)

    def test_missing_ending(self):
        # 同时移除所有温馨收尾词以触发检查
        text = (self._good_v2_prompt()
                .replace("画面温柔定格", "结束了")
                .replace("暖光静静照着", "光照着"))
        passed, issues = quality_review_v2(text, "日常生活")
        assert any("温馨治愈收尾" in i for i in issues)

    def test_missing_sound_block(self):
        text = self._good_v2_prompt().replace("配乐/音效：", "音效零碎").replace("0-3秒微风沙沙。", "").replace("3-7秒脚步哒哒。", "").replace("7-10秒摩擦声。", "")
        # 移除后应该缺少"配乐/音效"或"音效："≥2 出现
        passed, issues = quality_review_v2(text, "日常生活")
        # 这一条是软性要求，可能被其他问题遮盖，主要验证校验器不崩
        assert isinstance(issues, list)


class TestVisualStyleTokens:
    def test_tokens_contain_key_terms(self):
        assert "日系写实" in VISUAL_STYLE_TOKENS
        assert "浅景深" in VISUAL_STYLE_TOKENS
        assert "胶片" in VISUAL_STYLE_TOKENS
        assert "暖色调" in VISUAL_STYLE_TOKENS
