# -*- coding: utf-8 -*-
"""
generation 版本路由 — 根据 config.GENERATION_VERSION 切换 v1/v2。

所有调用点统一从本模块 import，避免散落 if version == ... 判断。
默认 v1（稳定版）。设 GENERATION_VERSION=v2 切到新版。
"""

import logging
from tutu_core.config import GENERATION_VERSION

logger = logging.getLogger("tutu.generation_router")


if GENERATION_VERSION == "v2":
    from tutu_core.generation_v2 import (
        generate_schedule,
        generate_event_content,
        quality_review_v2 as quality_review,
        classify_event,
    )
    logger.info("使用 generation_v2（日系写实+15秒5段+独立音效段）")
else:
    from tutu_core.generation import (
        generate_schedule,
        generate_event_content,
        quality_review,
        classify_event,
    )
    logger.info("使用 generation v1（稳定版）")


__all__ = [
    "generate_schedule",
    "generate_event_content",
    "quality_review",
    "classify_event",
]
