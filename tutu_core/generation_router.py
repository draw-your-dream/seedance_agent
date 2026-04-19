# -*- coding: utf-8 -*-
"""
generation 版本路由 — 动态分派 v1/v2。

每次调用时读取 GENERATION_VERSION 环境变量，实现 runtime 切换。
模块引用做了缓存，不会重复 import。

用法：
    # 走环境变量（默认 v1）
    from tutu_core.generation_router import generate_event_content
    content = generate_event_content(event, date_str)

    # 显式指定版本（A/B 测试等场景）
    content = generate_event_content(event, date_str, version="v2")

    # 或脚本里切：
    os.environ["GENERATION_VERSION"] = "v2"
    content = generate_event_content(event, date_str)  # 立刻生效
"""

import os
import logging
from functools import lru_cache

logger = logging.getLogger("tutu.generation_router")


_LOGGED_VERSIONS: set[str] = set()


@lru_cache(maxsize=2)
def _get_module(version: str):
    """根据版本名返回对应模块（缓存，避免重复 import）。"""
    if version == "v2":
        from tutu_core import generation_v2 as mod
        return mod
    from tutu_core import generation as mod
    return mod


def _resolve_version(explicit: str | None = None) -> str:
    """解析当前应使用的版本名。优先级：显式参数 > 环境变量 > 默认 v1。"""
    v = (explicit or os.environ.get("GENERATION_VERSION", "v1")).lower()
    if v not in ("v1", "v2"):
        logger.warning(f"未知 GENERATION_VERSION={v!r}，fallback 到 v1")
        v = "v1"
    # 版本首次使用时记一条日志（避免每次调用都打扰）
    if v not in _LOGGED_VERSIONS:
        _LOGGED_VERSIONS.add(v)
        if v == "v2":
            logger.info("使用 generation_v2（日系写实+15秒5段+独立音效段）")
        else:
            logger.info("使用 generation v1（稳定版）")
    return v


# ============================================================
# 公共 API（动态分派）
# ============================================================

def generate_schedule(*args, version: str | None = None, **kwargs):
    """日程生成。v1/v2 逻辑一致，统一走 v1。"""
    # schedule 生成与风格无关，v2 也是直接复用 v1 实现
    from tutu_core.generation import generate_schedule as _impl
    return _impl(*args, **kwargs)


def generate_event_content(*args, version: str | None = None, **kwargs):
    """事件视频 prompt 生成。按 version 分派到 v1 或 v2 模块。"""
    v = _resolve_version(version)
    return _get_module(v).generate_event_content(*args, **kwargs)


def quality_review(prompt_text: str, category: str, version: str | None = None):
    """质量校验。v1 用 quality_review，v2 用 quality_review_v2。"""
    v = _resolve_version(version)
    mod = _get_module(v)
    if v == "v2":
        return mod.quality_review_v2(prompt_text, category)
    return mod.quality_review(prompt_text, category)


def classify_event(*args, **kwargs):
    """分类函数 v1/v2 共用同一实现。"""
    from tutu_core.generation import classify_event as _impl
    return _impl(*args, **kwargs)


__all__ = [
    "generate_schedule",
    "generate_event_content",
    "quality_review",
    "classify_event",
]
