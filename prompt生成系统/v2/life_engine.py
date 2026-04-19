#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
秃秃生活引擎 v2

核心流程：
  1. 收集上下文（时间/天气/用户互动/热点/历史）
  2. LLM生成今日日程（3-5条事件）
  3. LLM为每条事件生成视频prompt + 心理活动
  4. 走v1 pipeline校验+提交+下载
  5. 输出完整的一天内容

使用:
  python life_engine.py generate --date 2026-04-09
  python life_engine.py generate --date today --user-msg "这个沙滩很好玩哦"
"""

import json
import sys
from pathlib import Path
from datetime import datetime, date

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tutu_core.config import (
    V2_DIR, VIDEO_DIR, REF_IMAGE,
    PERSONALITY_FILE, IP_CONSTITUTION_FILE,
    LIFE_JOURNAL_FILE, USER_MEMORY_FILE, DAILY_SIGNALS_FILE,
    DB_PATH,
)
from tutu_core.llm_client import call_llm, extract_json
from tutu_core.generation_router import generate_schedule as _core_generate_schedule
from tutu_core.generation_router import generate_event_content as _core_generate_event_content
from tutu_core.validators import validate_prompt
from tutu_core.seedance_client import (
    load_reference_image, submit_task, query_task, download_video,
)

# 可选导入 database 模块（CLI 场景下 DB 可能未初始化）
_db_mod = None
def _get_db():
    global _db_mod
    if _db_mod is not None:
        return _db_mod
    if not DB_PATH.exists():
        return None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "app"))
        import database as db_module
        _db_mod = db_module
        return _db_mod
    except Exception:
        return None


# ============================================================
# 数据加载
# ============================================================

def load_json(path, default=None):
    if default is None:
        default = []
    if not Path(path).exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_text(path):
    if not Path(path).exists():
        return ""
    return Path(path).read_text(encoding="utf-8")


def get_recent_journal(days=7):
    """获取最近事件摘要。优先从DB读取，降级到JSON。"""
    db = _get_db()
    if db:
        try:
            result = db.get_recent_journal(days)
            if result and not result.startswith("（"):
                return result
        except Exception:
            pass

    # 降级到 JSON
    journal = load_json(LIFE_JOURNAL_FILE)
    recent = journal[-days * 5:] if journal else []
    if not recent:
        return "（秃秃刚开始记录生活，还没有历史）"
    lines = []
    for entry in recent:
        lines.append(f"[{entry.get('date', '')} {entry.get('time', '')}] {entry.get('title', '')}: {entry.get('summary', '')}")
    return "\n".join(lines[-15:])


def get_recent_user_interactions():
    """获取用户互动。优先从DB的messages表读取，降级到user_memory.json。"""
    db = _get_db()
    if db:
        try:
            result = db.get_recent_interactions(10)
            if result and not result.startswith("（"):
                return result
        except Exception:
            pass

    # 降级到 JSON
    memory = load_json(USER_MEMORY_FILE)
    recent = memory[-10:] if memory else []
    if not recent:
        return "（用户还没有跟秃秃互动过）"
    lines = []
    for msg in recent:
        if msg.get("type") == "text":
            lines.append(f"[{msg.get('time', '')}] 用户说：{msg.get('content', '')}")
        elif msg.get("type") == "image":
            lines.append(f"[{msg.get('time', '')}] 用户发了一张图片：{msg.get('description', '')}")
    return "\n".join(lines)


# ============================================================
# Step 1: 生成今日日程
# ============================================================

def generate_daily_schedule(today_str, weather="", hot_topics="", user_msg="", user_img_desc="", user_city=""):
    """使用统一的 generation 模块生成日程。"""
    past_journal = get_recent_journal()
    user_interactions = get_recent_user_interactions()

    if user_msg:
        user_interactions += f"\n[今天] 用户说：{user_msg}"
    if user_img_desc:
        user_interactions += f"\n[今天] 用户发了一张图片：{user_img_desc}"

    print("  调用LLM生成日程...")
    return _core_generate_schedule(
        date_str=today_str, weather=weather, user_city=user_city,
        hot_signals=hot_topics, interactions=user_interactions, journal=past_journal,
    )


def generate_event_content(event, today_str, user_interactions_str):
    """使用统一的 generation 模块生成事件内容。"""
    return _core_generate_event_content(event, today_str, interactions=user_interactions_str)


# ============================================================
# Step 5: 记录用户互动
# ============================================================

def add_user_interaction(msg_type, content, description=""):
    memory = load_json(USER_MEMORY_FILE)
    memory.append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "type": msg_type,
        "content": content,
        "description": description
    })
    memory = memory[-150:]
    save_json(USER_MEMORY_FILE, memory)


# ============================================================
# 主流程: generate
# ============================================================

def cmd_generate(args):
    today = args.date if args.date != "today" else date.today().isoformat()
    print(f"\n{'='*60}")
    print(f"秃秃生活引擎 v2 — {today}")
    print(f"{'='*60}")

    if args.user_msg:
        add_user_interaction("text", args.user_msg)
        print(f"  记录用户消息: {args.user_msg}")
    if args.user_img_desc:
        add_user_interaction("image", "", args.user_img_desc)
        print(f"  记录用户图片: {args.user_img_desc}")

    # 读取热点信号
    hot_topics = args.hot_topics or ""
    signals_data = load_json(DAILY_SIGNALS_FILE)
    today_signals = [s for s in signals_data if s.get("date") == today]
    if today_signals:
        signal_scenes = [sig["scene"] for sig in today_signals[0].get("signals", [])]
        hot_from_skill = "；".join(signal_scenes)
        hot_topics = f"{hot_topics}；{hot_from_skill}" if hot_topics else hot_from_skill
        print(f"  读取到热点信号: {len(signal_scenes)}条")
    else:
        print(f"  无热点信号（可先运行 python trend_skill.py run）")

    # Step 1: 生成日程
    print(f"\n--- Step 1: 生成日程 ---")
    schedule = generate_daily_schedule(
        today_str=today, weather=args.weather or "",
        hot_topics=hot_topics, user_msg=args.user_msg or "",
        user_img_desc=args.user_img_desc or "", user_city=args.user_city or ""
    )

    if not schedule:
        print("  ❌ 日程生成失败")
        sys.exit(1)

    print(f"  生成 {len(schedule)} 条事件:")
    for e in schedule:
        flag = "👤" if e.get("user_related") else "🍄"
        print(f"    {flag} {e['time']} {e['title']} — {e['summary']}")

    # Step 2: 生成内容
    print(f"\n--- Step 2: 生成视频prompt + 心理活动 ---")
    user_interactions_str = get_recent_user_interactions()
    if args.user_msg:
        user_interactions_str += f"\n[今天] 用户说：{args.user_msg}"
    if args.user_img_desc:
        user_interactions_str += f"\n[今天] 用户发了图片：{args.user_img_desc}"

    events_with_content = []
    for i, event in enumerate(schedule):
        print(f"  [{i+1}/{len(schedule)}] {event['title']}...")
        content = generate_event_content(event, today, user_interactions_str)
        if not content:
            print(f"    ❌ 内容生成失败，跳过")
            continue

        prompt = content.get("video_prompt", "")
        voice = content.get("inner_voice", "")

        thoughts = content.get("thoughts", [])
        if not thoughts and voice:
            thoughts = [{"time": event["time"], "text": voice[:60]}]

        passed, issues = validate_prompt(prompt)
        if not passed:
            print(f"    ⚠️ 校验问题: {', '.join(issues)}")
        else:
            print(f"    ✅ 校验通过 ({len(prompt)}字)")
        print(f"    💭 {voice}")

        events_with_content.append({
            **event, "video_prompt": prompt, "inner_voice": voice,
            "thoughts": thoughts,
            "validation_passed": passed, "validation_issues": issues
        })

    # Step 3: 提交Seedance
    print(f"\n--- Step 3: 提交Seedance ---")
    img_b64 = load_reference_image()
    results = []
    for i, event in enumerate(events_with_content):
        if not event.get("validation_passed"):
            print(f"  ⏭ {event['title']}: 校验未通过，跳过提交")
            results.append({**event, "task_id": None, "status": "skipped"})
            continue

        task_id, error = submit_task(event["video_prompt"], img_b64, payload_tag=f"v2_{i}")
        if task_id:
            print(f"  ✅ {event['time']} {event['title']} -> {task_id}")
            results.append({**event, "task_id": task_id, "status": "submitted"})
        else:
            print(f"  ❌ {event['title']}: {error}")
            results.append({**event, "task_id": None, "status": "error", "error": error})

    # 保存
    tasks_file = f"/tmp/v2_{today}_tasks.json"
    save_json(tasks_file, results)
    print(f"\n  任务保存到: {tasks_file}")

    # 写入生活日志（JSON，保持向后兼容）
    journal = load_json(LIFE_JOURNAL_FILE)
    for r in results:
        journal.append({
            "date": today, "time": r.get("time", ""), "title": r.get("title", ""),
            "summary": r.get("summary", ""), "inner_voice": r.get("inner_voice", ""),
            "thoughts": r.get("thoughts", []),
            "user_related": r.get("user_related", False),
            "task_id": r.get("task_id"), "status": r.get("status")
        })
    save_json(LIFE_JOURNAL_FILE, journal)
    print(f"  生活日志已更新: {LIFE_JOURNAL_FILE}")

    # 同时写入 DB（如果可用）
    db = _get_db()
    if db:
        try:
            for i, r in enumerate(results):
                evt_id = f"evt_{today}_{r.get('time', '0000').replace(':', '')}_{i}"
                publish_at = f"{today}T{r.get('time', '00:00')}:00"
                db.insert_event({
                    "id": evt_id, "date": today, "time": r.get("time", ""),
                    "publish_at": publish_at, "title": r.get("title", ""),
                    "summary": r.get("summary", ""), "inner_voice": r.get("inner_voice", ""),
                    "thoughts": r.get("thoughts", []), "weather": args.weather or "",
                    "video_prompt": r.get("video_prompt", ""), "video_url": "",
                    "video_status": r.get("status", "pending"), "task_id": r.get("task_id", ""),
                    "triggered_by": r.get("triggered_by", "daily"),
                    "user_related": 1 if r.get("user_related") else 0, "is_new": 0,
                })
            print(f"  DB已同步更新")
        except Exception as e:
            print(f"  ⚠️ DB写入失败(非致命): {e}")

    # 输出预览
    print(f"\n{'='*60}")
    print(f"秃秃的一天 — {today}")
    print(f"{'='*60}")
    for r in results:
        flag = "👤" if r.get("user_related") else "🍄"
        status = "✅" if r.get("task_id") else "❌"
        print(f"\n  {flag} {r['time']} | {r['title']}")
        print(f"  {status} {r.get('task_id', r.get('status', ''))}")
        print(f"  💭 {r.get('inner_voice', '')}")

    print(f"\n  视频生成需要3-8分钟，完成后运行:")
    print(f"  python life_engine.py download --tasks {tasks_file}")


# ============================================================
# 下载命令
# ============================================================

def cmd_download(args):
    tasks = load_json(args.tasks)
    print(f"下载 {len(tasks)} 条视频...\n")

    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    for t in tasks:
        if not t.get("task_id"):
            continue
        title = t.get("title", "unknown").replace("/", "_").replace(" ", "_")
        time_str = t.get("time", "0000").replace(":", "")
        date_str = t.get("date", "")
        filename = f"{date_str}_{time_str}_{title}.mp4"

        d = query_task(t["task_id"])
        if d.get("status") == "succeeded":
            url = d["content"]["video_url"]
            ok, info = download_video(url, VIDEO_DIR / filename)
            print(f"  {'✅' if ok else '❌'} {t['time']} {t['title']}: {info}")
        else:
            print(f"  ⏳ {t['time']} {t['title']}: {d.get('status', 'unknown')}")


# ============================================================
# CLI
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="秃秃生活引擎 v2")
    sub = parser.add_subparsers(dest="command")

    p_gen = sub.add_parser("generate", help="生成今日内容")
    p_gen.add_argument("--date", default="today")
    p_gen.add_argument("--weather", help="天气描述")
    p_gen.add_argument("--hot-topics", help="当日热点")
    p_gen.add_argument("--user-msg", help="用户消息")
    p_gen.add_argument("--user-img-desc", help="用户图片描述")
    p_gen.add_argument("--user-city", help="用户城市")

    p_dl = sub.add_parser("download", help="下载视频")
    p_dl.add_argument("--tasks", required=True)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    {"generate": cmd_generate, "download": cmd_download}[args.command](args)


if __name__ == "__main__":
    main()
