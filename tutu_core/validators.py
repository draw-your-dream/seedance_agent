# -*- coding: utf-8 -*-
"""统一Prompt校验器 — 合并自 seedance_pipeline / life_engine / generate_batch 的校验逻辑"""

import re
import logging

from tutu_core.config import (
    FORBIDDEN_WORDS, AGGRESSIVE_WORDS, NEGATION_KEYWORDS,
    REQUIRED_SUFFIXES, REQUIRED_PREFIX,
    PROMPT_MIN_LENGTH, PROMPT_MAX_LENGTH,
)

logger = logging.getLogger("tutu.validators")


def validate_prompt(text: str) -> tuple[bool, list[str]]:
    """
    对单条prompt文本执行完整质量校验。

    返回 (passed, issues)：
    - passed=True 仅当没有 ❌ 错误（⚠️ 警告允许通过）
    - issues 为所有检查项的列表

    修复说明：
    - 原版 seedance_pipeline.py:196 的返回值逻辑有bug：
      `return len(issues) == 0 or all(i.startswith("⚠️") for i in issues)`
      这导致只有警告时也算"通过"，但调用方的判断与此不一致。
      现在明确分离 ❌ 错误和 ⚠️ 警告。
    """
    issues = []

    # 1. 文本必须以"图片1"开头（防元数据污染 — Batch 1教训）
    if not text.startswith(REQUIRED_PREFIX):
        issues.append(f"❌ 文本未以'{REQUIRED_PREFIX}'开头: '{text[:30]}...'")

    # 2. 必须包含末尾声明
    for suffix in REQUIRED_SUFFIXES:
        if suffix not in text:
            issues.append(f"❌ 缺少末尾声明关键词「{suffix}」")

    # 3. 字数检查
    length = len(text)
    if length < PROMPT_MIN_LENGTH:
        issues.append(f"❌ 文本太短({length}字)，最少{PROMPT_MIN_LENGTH}字")
    elif length > PROMPT_MAX_LENGTH:
        issues.append(f"⚠️ 文本偏长({length}字)，建议不超过{PROMPT_MAX_LENGTH}字")

    # 4. 时间码检查
    if not re.search(r'\d+-\d+s', text):
        issues.append("❌ 缺少时间码（如0-3s、3-7s）")

    # 5. 音效检查
    if "音效" not in text:
        issues.append("❌ 缺少音效描写")

    # 6. 禁止词检查（只在非否定句中检查）
    for word in FORBIDDEN_WORDS:
        for line in text.split("\n"):
            if word in line:
                is_negation = any(nk in line for nk in NEGATION_KEYWORDS)
                if not is_negation:
                    issues.append(f"❌ 禁止词「{word}」出现在非否定句: ...{line.strip()[:40]}...")

    # 7. 过激情绪检查（Batch 2闹钟教训）
    for word in AGGRESSIVE_WORDS:
        if word in text:
            issues.append(f"❌ 过激情绪词「{word}」")

    # 8. 互动beat检查
    last_lines = text.split("\n")[-3:]
    last_text = " ".join(last_lines)
    if not any(kw in last_text for kw in ["镜头", "定格", "画面"]):
        issues.append("⚠️ 结尾可能缺少互动beat或画面定格")

    # 明确分离错误和警告
    errors = [i for i in issues if i.startswith("❌")]
    passed = len(errors) == 0
    return passed, issues


def quick_validate(prompt_text: str) -> dict:
    """
    快速本地校验（不调用LLM），用于 batch_generator。
    返回 {"passed": bool, "issues": list, "score": int}
    """
    issues = []

    # 禁止词检查
    for word in FORBIDDEN_WORDS:
        for line in prompt_text.split("\n"):
            if word in line:
                is_negation = any(nk in line for nk in NEGATION_KEYWORDS)
                if not is_negation:
                    issues.append(f"发现禁止词「{word}」在: {line.strip()[:50]}")

    # 时间码检查
    has_timecode = any(
        pattern in prompt_text
        for pattern in ["0-", "秒：", "秒画面", "s：", "s:", "镜头1", "镜号1", "SHOT"]
    )
    if not has_timecode:
        issues.append("缺少时间码/分镜标注")

    # 构图检查
    if not any(kw in prompt_text for kw in ["中心对称", "构图", "中景", "近景", "远景", "特写"]):
        issues.append("缺少构图描述")

    # 音效检查
    if not any(kw in prompt_text for kw in ["音效", "声", "嘟", "duang", "咔", "滋"]):
        issues.append("缺少音效描述")

    # 角色大小检查
    if not any(kw in prompt_text for kw in ["不要太大", "三分之一", "4cm", "4厘米", "微缩"]):
        issues.append("缺少角色大小限制描述")

    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "score": max(0, 5 - len(issues)),
    }
