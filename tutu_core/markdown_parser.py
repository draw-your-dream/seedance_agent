# -*- coding: utf-8 -*-
"""Markdown Prompt文件解析器 — 统一的MD提取逻辑"""

import re
import logging

logger = logging.getLogger("tutu.parser")


def extract_prompts(filepath: str) -> list[dict]:
    """
    从markdown文件提取每条prompt条目。

    支持的md格式：
        ### 16 | 清晨 · 秃秃和露珠
        图片1是小蘑菇角色形象参考...（prompt正文）
        ---

    返回 [{"num": int, "title": str, "text": str}, ...]
    """
    with open(filepath, "r", encoding="utf-8") as f:
        raw = f.read()

    sections = re.split(r'(?:^|\n)### ', raw)
    prompts = []

    for sec in sections:
        # 必须包含时间码才是有效prompt段
        if not re.search(r'\d+-\d+s', sec):
            continue

        # 提取编号和标题
        m = re.match(r'(\d+) \| (.+)', sec.strip())
        if not m:
            continue
        num = int(m.group(1))
        title = m.group(2).strip()

        # 从"图片1"开始截取，排除前面的元数据（Batch 1教训）
        # 要求"图片1"出现在行首（避免匹配引用文字中的"图片1"）
        prompt_match = re.search(r'(?:^|\n)(图片1)', sec)
        if not prompt_match:
            logger.warning(f"#{num} 找不到'图片1'开头，跳过")
            continue
        prompt_start = prompt_match.start(1)

        text = sec[prompt_start:].strip()
        # 去掉尾部可能残留的 ---
        text = re.sub(r'\n---\s*$', '', text).strip()

        prompts.append({"num": num, "title": title, "text": text})

    return prompts
