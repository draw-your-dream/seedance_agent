# -*- coding: utf-8 -*-
"""tutu_core.markdown_parser 单元测试"""

import tempfile
import os
import pytest
from tutu_core.markdown_parser import extract_prompts


def _write_temp_md(content: str) -> str:
    """写临时MD文件，返回路径"""
    fd, path = tempfile.mkstemp(suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class TestExtractPrompts:
    def test_single_prompt(self):
        md = """# 标题

### 1 | 秃秃吃蛋糕

图片1是小蘑菇角色形象参考。

0-3s：蘑菇走路。

---
"""
        path = _write_temp_md(md)
        try:
            prompts = extract_prompts(path)
            assert len(prompts) == 1
            assert prompts[0]["num"] == 1
            assert prompts[0]["title"] == "秃秃吃蛋糕"
            assert prompts[0]["text"].startswith("图片1")
            assert "---" not in prompts[0]["text"]
        finally:
            os.unlink(path)

    def test_multiple_prompts(self):
        md = """# Batch

### 11 | 秃秃荡秋千

图片1是参考。0-3s：荡。3-7s：高。

---

### 12 | 秃秃踩水坑

图片1是参考。0-3s：踩。3-7s：溅。

---
"""
        path = _write_temp_md(md)
        try:
            prompts = extract_prompts(path)
            assert len(prompts) == 2
            assert prompts[0]["num"] == 11
            assert prompts[1]["num"] == 12
        finally:
            os.unlink(path)

    def test_skips_non_prompt_sections(self):
        md = """# 说明

这是一段说明文字，不含时间码。

### 1 | 有效prompt

图片1参考。0-3s：动作。

---

### 补充说明

这段没有时间码，应该被跳过。
"""
        path = _write_temp_md(md)
        try:
            prompts = extract_prompts(path)
            assert len(prompts) == 1
        finally:
            os.unlink(path)

    def test_skips_without_image_prefix(self):
        md = """### 1 | 缺少图片引用

这段有 0-3s 时间码，但没有"图片1"开头。
"""
        path = _write_temp_md(md)
        try:
            prompts = extract_prompts(path)
            assert len(prompts) == 0
        finally:
            os.unlink(path)

    def test_strips_trailing_separator(self):
        md = """### 5 | 测试

图片1 参考。0-3s：走。

---
"""
        path = _write_temp_md(md)
        try:
            prompts = extract_prompts(path)
            assert len(prompts) == 1
            assert not prompts[0]["text"].endswith("---")
        finally:
            os.unlink(path)

    def test_empty_file(self):
        path = _write_temp_md("")
        try:
            prompts = extract_prompts(path)
            assert len(prompts) == 0
        finally:
            os.unlink(path)

    def test_preserves_multiline_content(self):
        md = """### 1 | 多行测试

图片1是角色参考。

0-3s：第一段。

3-7s：第二段动作描写。

---
"""
        path = _write_temp_md(md)
        try:
            prompts = extract_prompts(path)
            text = prompts[0]["text"]
            assert "0-3s" in text
            assert "3-7s" in text
        finally:
            os.unlink(path)
