#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
热点信号转化器 — 抓取网络热点，转化成秃秃能做的事

使用:
  python trend_skill.py run
  python trend_skill.py run --date 2026-04-09
"""

import json
import sys
from pathlib import Path
from datetime import date

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tutu_core.config import DAILY_SIGNALS_FILE
from tutu_core.llm_client import call_llm, extract_json


def fetch_and_convert(today_str):
    """一步完成：让LLM模拟抓取热点+转化信号"""

    prompt = f"""今天是{today_str}。

请完成两个任务：

任务1：列出今天中国互联网上可能的热点话题（基于你对这个日期的知识，如季节、节气、节日、常见社会话题等），列出5-8条。

任务2：你是秃秃的生活策划。秃秃是一个4cm高的小蘑菇角色，住在人类家里的窗台/桌面/花盆附近。请从任务1的热点中筛选出秃秃能做的事（最多3条），转化为具体的微缩生活场景。

筛选规则：
· 秃秃只能在家里活动（窗台/桌面/花盆/书架等）
· 不涉及政治/负面/争议/暴力话题
· 自然现象/季节变化/节日/可爱话题优先
· 转化为"秃秃做了什么"的具体画面，不是抽象概念
· 秃秃不吃东西

严格按JSON输出：
```json
{{
  "date": "{today_str}",
  "hot_topics": ["热点1", "热点2", ...],
  "signals": [
    {{
      "source_topic": "来源热点",
      "scene": "秃秃的具体场景描述（一句话）",
      "reason": "为什么选这个（一句话）"
    }}
  ]
}}
```"""

    print("  调用LLM分析热点...")
    result = call_llm("", prompt)

    if not result:
        return None

    try:
        return extract_json(result)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"  JSON解析失败: {e}")
        print(f"  原始输出: {result[:300]}")
        return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description="热点信号转化器")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--date", default="today")
    args = parser.parse_args()

    today_str = args.date if args.date != "today" else date.today().isoformat()
    print(f"热点信号转化器 — {today_str}\n")

    data = fetch_and_convert(today_str)
    if not data:
        print("❌ 转化失败")
        sys.exit(1)

    print(f"\n今日热点:")
    for t in data.get("hot_topics", []):
        print(f"  · {t}")

    print(f"\n转化为秃秃信号:")
    for s in data.get("signals", []):
        print(f"  🍄 {s['scene']}")
        print(f"     来源: {s['source_topic']} | {s['reason']}")

    # 保存（追加/替换同日数据）
    signals_file = DAILY_SIGNALS_FILE
    all_signals = []
    if signals_file.exists():
        with open(signals_file, "r", encoding="utf-8") as f:
            all_signals = json.load(f)

    all_signals = [s for s in all_signals if s.get("date") != today_str]
    all_signals.append(data)

    with open(signals_file, "w", encoding="utf-8") as f:
        json.dump(all_signals, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 信号已保存到 {signals_file}")
    print(f"   {len(data.get('signals', []))} 条信号可供生活调度器使用")


if __name__ == "__main__":
    main()
