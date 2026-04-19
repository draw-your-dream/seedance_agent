# -*- coding: utf-8 -*-
"""调度器 — 定时生成日程+下载视频（使用 tutu_core 统一生成逻辑）"""

import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, date

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tutu_core.config import PERSONALITY_FILE
from tutu_core.generation_router import generate_schedule, generate_event_content
from tutu_core.validators import validate_prompt
from tutu_core.seedance_client import (
    load_reference_image, load_all_reference_images,
    load_reference_images_for_prompt,
    submit_task, query_task, download_video,
)

import database as db

logger = logging.getLogger("tutu.scheduler")

VIDEOS_DIR = Path(__file__).parent / "static" / "videos"


# ============================================================
# 核心：生成今日日程
# ============================================================

def generate_daily(date_str=None, weather="", user_city="", hot_signals=""):
    """生成一天的内容"""
    if not date_str:
        date_str = date.today().isoformat()

    logger.info(f"开始生成 {date_str} 的日程")

    # 读取上下文
    interactions = db.get_recent_interactions(20)
    journal = db.get_recent_journal(7)

    # Step 1: 生成日程（使用统一的 generation 模块）
    schedule = generate_schedule(
        date_str=date_str, weather=weather, user_city=user_city,
        hot_signals=hot_signals, interactions=interactions, journal=journal,
    )
    if not schedule:
        logger.error("日程生成失败")
        return []

    logger.info(f"生成 {len(schedule)} 条事件")

    # Step 2: 为每条事件生成内容
    # 先确认主参考图存在（避免每次循环都抛异常）
    try:
        load_reference_image()
    except FileNotFoundError as e:
        logger.error(str(e))
        return []

    events = []
    for i, evt in enumerate(schedule):
        content = generate_event_content(evt, date_str, interactions=interactions)
        if not content:
            logger.warning(f"⏭ {evt['title']}: 内容生成失败")
            continue

        prompt_text = content["video_prompt"]
        voice = content.get("inner_voice", "")
        thoughts = content.get("thoughts", [])

        # 校验
        passed, issues = validate_prompt(prompt_text)
        if not passed:
            logger.warning(f"{evt['title']}: 校验问题 {issues}")

        # 按 prompt 内容动态选择参考图（含表情匹配）
        img_b64, ref_labels = load_reference_images_for_prompt(prompt_text)
        logger.info(f"{evt['title']}: 使用{len(img_b64)}张参考图 ({', '.join(ref_labels)})")

        # 提交Seedance
        task_id, error = submit_task(prompt_text, img_b64, payload_tag=f"sched_{i}")
        status = "generating" if task_id else "failed"
        if error:
            logger.error(f"{evt['title']}: {error}")

        is_user = evt.get("user_related", False)
        evt_id = f"evt_{date_str}_{evt['time'].replace(':', '')}_{i}"
        publish_at = f"{date_str}T{evt['time']}:00"

        event_data = {
            "id": evt_id, "date": date_str, "time": evt["time"],
            "publish_at": publish_at, "title": evt["title"],
            "summary": evt.get("summary", ""), "inner_voice": voice,
            "thoughts": thoughts, "weather": weather,
            "video_prompt": prompt_text, "video_status": status,
            "task_id": task_id or "", "triggered_by": evt.get("triggered_by", "daily"),
            "user_related": 1 if is_user else 0,
            "is_new": 0,
        }
        db.insert_event(event_data)
        events.append(event_data)

        flag = "✅" if task_id else "❌"
        logger.info(f"{flag} {evt['time']} {evt['title']} | 💭 {voice[:30]}...")

    return events


# ============================================================
# 下载轮询
# ============================================================

MAX_RETRIES = 3


def poll_and_download():
    """检查generating/failed状态的视频，下载完成的，重试失败的"""
    from database import db_conn
    with db_conn() as conn:
        # 同时查询 generating 和可重试的 failed 任务
        rows = conn.execute(
            "SELECT * FROM events WHERE task_id != '' AND "
            "(video_status = 'generating' OR (video_status = 'failed' AND COALESCE(retry_count, 0) < ?))",
            (MAX_RETRIES,)
        ).fetchall()

    if not rows:
        return

    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    rows = [dict(r) for r in rows]
    to_retry = [r for r in rows if r["video_status"] == "failed"]
    to_check = [r for r in rows if r["video_status"] == "generating"]

    # 失败任务：串行重试（涉及 Seedance 提交，并发度已由 Seedance API 限制）
    if to_retry:
        for r in to_retry:
            _retry_failed(r)

    # generating 任务：并发查询 + 下载
    if to_check:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_check_and_download, r) for r in to_check]
            for fut in as_completed(futures):
                msg = fut.result()
                if msg:
                    print(msg, flush=True)


def _retry_failed(r: dict):
    """失败任务重新提交到 Seedance。"""
    retry_count = r.get("retry_count", 0) or 0
    prompt_text = r.get("video_prompt", "")
    if not prompt_text:
        return
    try:
        img_b64, _ = load_reference_images_for_prompt(prompt_text)
        new_task_id, error = submit_task(prompt_text, img_b64, payload_tag=f"retry_{retry_count}")
        if new_task_id:
            _update_retry(r["id"], new_task_id, retry_count + 1)
            print(f"  🔄 重试 [{retry_count + 1}/{MAX_RETRIES}]: {r['title']} -> {new_task_id}", flush=True)
        else:
            logger.warning(f"重试提交失败: {r['title']}: {error}")
    except Exception as e:
        logger.error(f"重试异常: {r['title']}: {e}")


def _check_and_download(r: dict) -> str:
    """查询单个任务状态并下载。返回日志消息。"""
    retry_count = r.get("retry_count", 0) or 0
    d = query_task(r["task_id"])
    status = d.get("status", "unknown")

    if status == "succeeded":
        url = d["content"]["video_url"]
        fname = f"{r['id']}.mp4"
        fpath = VIDEOS_DIR / fname
        ok, info = download_video(url, fpath)
        if ok:
            db.update_event_video(r["id"], video_url=f"/static/videos/{fname}", video_status="ready")
            return f"  ✅ 下载完成: {r['title']} ({info})"
        else:
            db.update_event_video(r["id"], video_status="failed")
            return f"  ❌ 下载失败: {r['title']} ({info})"
    elif status == "failed":
        db.update_event_video(r["id"], video_status="failed")
        if retry_count < MAX_RETRIES:
            return f"  ⚠️ 生成失败(将重试): {r['title']}"
        else:
            return f"  ❌ 生成失败(已达最大重试): {r['title']}"
    return ""  # running 等状态不输出


def _update_retry(event_id, new_task_id, retry_count):
    """更新重试状态"""
    with db.db_conn() as conn:
        conn.execute(
            "UPDATE events SET task_id = ?, video_status = 'generating', retry_count = ? WHERE id = ?",
            (new_task_id, retry_count, event_id)
        )
        conn.commit()


# ============================================================
# 为聊天触发的单条视频
# ============================================================

def generate_single_event(description, date_str=None):
    """聊天触发的额外视频"""
    if not date_str:
        date_str = date.today().isoformat()
    now = datetime.now()
    time_str = now.strftime("%H:%M")

    evt = {"time": time_str, "title": description[:20], "summary": description,
           "triggered_by": "user", "user_related": True}

    content = generate_event_content(evt, date_str)
    if not content:
        return None

    try:
        img_b64, _ = load_reference_images_for_prompt(content["video_prompt"])
        task_id, error = submit_task(content["video_prompt"], img_b64, payload_tag="chat_trigger")
    except FileNotFoundError:
        task_id, error = None, "参考图片不存在"

    voice = content.get("inner_voice", "")
    thoughts = content.get("thoughts", [])

    evt_id = f"evt_{date_str}_{time_str.replace(':', '')}_user"
    publish_at = datetime.now().isoformat()

    event_data = {
        "id": evt_id, "date": date_str, "time": time_str,
        "publish_at": publish_at, "title": description[:20],
        "summary": description, "inner_voice": voice,
        "thoughts": thoughts, "weather": "",
        "video_prompt": content["video_prompt"],
        "video_status": "generating" if task_id else "failed",
        "task_id": task_id or "", "triggered_by": "user", "user_related": 1,
        "is_new": 1,
    }
    db.insert_event(event_data)
    return event_data
