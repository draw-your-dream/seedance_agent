#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A/B 对比测试：旧 prompt vs 新 prompt（含分类+范例注入+质量校验）

对同一组事件分别用两种方式生成 video prompt，对比质量指标。
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tutu_core.llm_client import call_llm, extract_json
from tutu_core.generation import (
    generate_event_content,
    quality_review,
    classify_event,
    _load_cached,
)
from tutu_core.config import PERSONALITY_FILE

# ============================================================
# 旧方式：通用 prompt（从之前的 scheduler.py 复原）
# ============================================================

def generate_old(event, date_str, interactions=""):
    """旧方式：通用 prompt，无分类，无范例。"""
    personality = ""
    if PERSONALITY_FILE.exists():
        personality = PERSONALITY_FILE.read_text(encoding="utf-8")

    prompt = f"""{personality}

为秃秃的一个生活事件生成：1）视频prompt 2）心理活动文案 3）碎碎念时间线

视频prompt规则：
· 以"图片1是小蘑菇角色形象参考。"开头
· 微缩场景，4cm高，中近景，不超过画面三分之一
· 4段时间码：0-3s / 3-7s / 7-10s / 10-13s，每段带音效
· 末尾写"只要音效，不要背景音乐，不要字幕。注意：小蘑菇没有牙齿（嘴巴张开时里面是黑色的）、没有舌头、没有眉毛、没有尾巴、没有手指（手是圆圆的像毛绒玩偶）。"
· 500-700字，不要吃东西画面，角色没有手指（手是圆圆的毛绒手），没有牙齿（嘴巴张开里面是黑色的）

心理活动(inner_voice)：30-60字，第一人称，口语化，偶尔用"嘟"
碎碎念(thoughts)：2-3条短句，每条10-25字，带具体时间，是inner_voice的拆分版

事件：{event['time']} {event['title']} — {event.get('summary', '')}
用户相关：{event.get('user_related', False)}
用户最近说：{interactions[-200:] if interactions else '无'}

JSON格式：
```json
{{"video_prompt":"图片1是小蘑菇角色形象参考。......","inner_voice":"......","thoughts":[{{"time":"{event['time']}","text":"短句1"}},{{"time":"{event['time']}","text":"短句2"}}]}}
```"""

    raw = call_llm("", prompt, use_cache=False)
    if not raw:
        return None
    try:
        content = extract_json(raw)
        if not content.get("video_prompt", "").startswith("图片1"):
            return None
        return content
    except (json.JSONDecodeError, ValueError):
        return None


# ============================================================
# 测试事件
# ============================================================

TEST_EVENTS = [
    {
        "time": "09:00",
        "title": "秃秃第一次认识订书机",
        "summary": "在桌上发现订书机，好奇地按了一下被弹飞",
        "triggered_by": "daily",
        "user_related": False,
    },
    {
        "time": "14:00",
        "title": "秃秃荡秋千",
        "summary": "在公园的小秋千上荡来荡去，越荡越高",
        "triggered_by": "daily",
        "user_related": False,
    },
    {
        "time": "16:00",
        "title": "秃秃给你冲奶茶",
        "summary": "用户说想喝奶茶，秃秃决定亲自冲一杯",
        "triggered_by": "user",
        "user_related": True,
    },
]

DATE_STR = "2026-04-12"
INTERACTIONS = "[2026-04-12 15:30] 用户说：好想喝奶茶啊"


# ============================================================
# 运行对比
# ============================================================

def count_metric(text, words):
    return sum(text.count(w) for w in words)


def analyze(prompt_text, category):
    """分析一段 prompt 的质量指标。"""
    import re
    timecodes = len(re.findall(r'\d+-\d+s|镜\d|镜头\d|第[一二三四五六]段', prompt_text))
    sound_words = ["音效", "声", "duang", "啪", "咔", "嘎", "噗", "叮", "咕", "滋", "嘟", "boing", "啵", "沙沙", "咚"]
    sound = count_metric(prompt_text, sound_words)
    expr_words = ["眼睛", "腮帮", "嘴", "帽子", "表情", "眯", "鼓", "歪头", "愣", "笑", "脸蛋", "点头", "摇头", "眨眼"]
    expr = count_metric(prompt_text, expr_words)
    interact_words = ["镜头", "看向", "递向", "推向", "挥手", "眨眼", "定格", "望向", "蹭", "安心", "满足"]
    interact = count_metric(prompt_text, interact_words)
    passed, issues = quality_review(prompt_text, category)
    return {
        "字数": len(prompt_text),
        "分段数": timecodes,
        "音效词数": sound,
        "表情词数": expr,
        "互动词数": interact,
        "质量通过": passed,
        "问题数": len(issues),
        "问题": issues,
    }


def main():
    print("=" * 70)
    print("A/B 对比：旧 prompt（通用） vs 新 prompt（分类+范例+质量校验）")
    print("=" * 70)

    for evt in TEST_EVENTS:
        category = classify_event(evt["title"], evt.get("summary", ""))
        print(f"\n{'─'*70}")
        print(f"事件: {evt['title']} | 分类: {category}")
        print(f"{'─'*70}")

        # A: 旧方式
        print("\n  [A] 旧方式生成中...")
        old = generate_old(evt, DATE_STR, INTERACTIONS)
        if not old:
            print("  ❌ 旧方式生成失败")
            old_prompt = ""
        else:
            old_prompt = old.get("video_prompt", "")

        # B: 新方式
        print("  [B] 新方式生成中...")
        new = generate_event_content(evt, DATE_STR, interactions=INTERACTIONS, max_attempts=2)
        if not new:
            print("  ❌ 新方式生成失败")
            new_prompt = ""
        else:
            new_prompt = new.get("video_prompt", "")

        if not old_prompt and not new_prompt:
            print("  两种方式都失败，跳过")
            continue

        # 分析
        old_m = analyze(old_prompt, category) if old_prompt else None
        new_m = analyze(new_prompt, category) if new_prompt else None

        print(f"\n  {'指标':<10} {'旧方式':>8} {'新方式':>8} {'差异':>8}")
        print(f"  {'─'*40}")
        if old_m and new_m:
            for key in ["字数", "分段数", "音效词数", "表情词数", "互动词数", "问题数"]:
                o = old_m[key]
                n = new_m[key]
                diff = n - o
                arrow = "↑" if diff > 0 else ("↓" if diff < 0 else "=")
                # 问题数越少越好，其他越多越好
                if key == "问题数":
                    color = "✅" if diff <= 0 else "⚠️"
                else:
                    color = "✅" if diff >= 0 else "⚠️"
                print(f"  {key:<10} {o:>8} {n:>8} {arrow:>4}{abs(diff):>3} {color}")

            print(f"\n  旧方式质量通过: {'✅' if old_m['质量通过'] else '❌'}")
            print(f"  新方式质量通过: {'✅' if new_m['质量通过'] else '❌'}")

            if old_m["问题"]:
                print(f"\n  旧方式问题:")
                for i in old_m["问题"]:
                    print(f"    - {i}")
            if new_m["问题"]:
                print(f"\n  新方式问题:")
                for i in new_m["问题"]:
                    print(f"    - {i}")
        elif old_m:
            print(f"  旧方式: {old_m['字数']}字 | 新方式: 生成失败")
        elif new_m:
            print(f"  旧方式: 生成失败 | 新方式: {new_m['字数']}字")

        # 输出 prompt 前100字对比
        print(f"\n  --- 旧方式 prompt 前150字 ---")
        print(f"  {old_prompt[:150]}..." if old_prompt else "  (无)")
        print(f"\n  --- 新方式 prompt 前150字 ---")
        print(f"  {new_prompt[:150]}..." if new_prompt else "  (无)")

    print(f"\n{'='*70}")
    print("对比完成")


if __name__ == "__main__":
    main()
