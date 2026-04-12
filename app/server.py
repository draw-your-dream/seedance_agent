# -*- coding: utf-8 -*-
"""
秃秃生活App — FastAPI服务器

启动: python server.py
访问: http://localhost:8000 (手机同局域网访问 http://<电脑IP>:8000)
"""

import asyncio
import os
import shutil
import sys
import threading
import time as time_mod
import logging
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tutu_core.config import ADMIN_API_KEY, POLL_INTERVAL

from fastapi import FastAPI, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import database as db
import chat as chat_service
import scheduler

logger = logging.getLogger("tutu.server")

APP_DIR = Path(__file__).parent
VIDEOS_SRC = APP_DIR.parent / "prompt生成系统" / "output" / "videos"
STATIC_DIR = APP_DIR / "static"
STATIC_VIDEOS = STATIC_DIR / "videos"
UPLOADS_DIR = STATIC_DIR / "uploads"


def setup_static():
    """创建static目录，软链接历史视频"""
    STATIC_DIR.mkdir(exist_ok=True)
    STATIC_VIDEOS.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(exist_ok=True)

    # 把历史视频软链接过来（修复：symlink失败时用copy兜底）
    if VIDEOS_SRC.exists():
        for mp4 in VIDEOS_SRC.glob("*.mp4"):
            link = STATIC_VIDEOS / mp4.name
            if not link.exists():
                try:
                    link.symlink_to(mp4)
                except OSError:
                    shutil.copy2(mp4, link)

    # reference图片
    ref_src = APP_DIR.parent / "reference.png"
    ref_dst = STATIC_DIR / "reference.png"
    if ref_src.exists() and not ref_dst.exists():
        try:
            ref_dst.symlink_to(ref_src)
        except OSError:
            shutil.copy2(ref_src, ref_dst)


# ============================================================
# 后台轮询线程（修复：异常恢复）
# ============================================================

def _poll_loop():
    """定期检查是否有视频需要下载"""
    while True:
        try:
            scheduler.poll_and_download()
        except Exception as e:
            logger.error(f"[轮询] 错误: {e}", exc_info=True)
        time_mod.sleep(POLL_INTERVAL)


# ============================================================
# Admin鉴权
# ============================================================

def _check_admin(api_key: str = None) -> bool:
    """验证admin API密钥"""
    if not ADMIN_API_KEY:
        return True  # 未配置密钥时跳过验证（开发模式）
    return api_key == ADMIN_API_KEY


# ============================================================
# FastAPI App
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_static()
    db.init_db()
    db.seed_existing_videos()
    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()
    print("\n  🍄 秃秃生活App启动")
    print(f"  📱 http://localhost:8000")
    print(f"  📱 手机访问: http://<电脑IP>:8000\n")
    yield
    print("  关闭中...")


app = FastAPI(title="秃秃生活App", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ============================================================
# 首页
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = APP_DIR / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


def _normalize_event(e):
    """统一事件输出格式：修正video_url、确保thoughts存在"""
    if e.get("video_url") and not e["video_url"].startswith("/static"):
        fname = e["video_url"].split("/")[-1]
        e["video_url"] = f"/static/videos/{fname}"
    # thoughts 已在 _row_to_event 中处理，这里确保兜底
    if "thoughts" not in e or e["thoughts"] is None:
        voice = e.get("inner_voice", "")
        e["thoughts"] = [{"time": e.get("time", ""), "text": voice}] if voice else []
    return e


# ============================================================
# Feed API
# ============================================================

@app.get("/api/feed")
async def get_feed(before: str = None, limit: int = 10):
    events = db.get_feed(before=before, limit=limit)
    events = [_normalize_event(e) for e in events]
    return {"events": events, "has_more": len(events) == limit}


@app.get("/api/feed/today")
async def get_today():
    events = db.get_today_events()
    events = [_normalize_event(e) for e in events]
    return {"events": events}


# ============================================================
# Timeline API（统一替代 timeline.json / timeline_data.json）
# ============================================================

@app.get("/api/timeline")
async def get_timeline(days: int = 7):
    """按天分组的时间线，兼容 v2_demo/app.html 的数据格式"""
    from datetime import timedelta
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    all_events = db.get_events_by_date_range(start_date, end_date, limit=100)

    # 按日期分组
    from collections import OrderedDict
    days_map = OrderedDict()
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    for e in all_events:
        d = e["date"]
        if d not in days_map:
            try:
                dt = datetime.strptime(d, "%Y-%m-%d")
                weekday = weekday_names[dt.weekday()]
            except ValueError:
                weekday = ""
            days_map[d] = {"date": d, "weekday": weekday, "slots": []}
        evt = _normalize_event(e)
        days_map[d]["slots"].append({
            "time": evt.get("time", ""),
            "period": _time_to_period(evt.get("time", "")),
            "weather": evt.get("weather", ""),
            "title": evt.get("title", ""),
            "video_url": evt.get("video_url", ""),
            "video_status": evt.get("video_status", ""),
            "thoughts": evt.get("thoughts", []),
            "inner_voice": evt.get("inner_voice", ""),
            "triggered_by": evt.get("triggered_by", "daily"),
            "is_new": bool(evt.get("is_new", 0)),
            "user_related": bool(evt.get("user_related", 0)),
        })

    return list(days_map.values())


def _time_to_period(time_str):
    """时间字符串转时段名"""
    try:
        hour = int(time_str.split(":")[0])
    except (ValueError, IndexError):
        return ""
    if hour < 9:
        return "早晨"
    elif hour < 12:
        return "上午"
    elif hour < 14:
        return "中午"
    elif hour < 18:
        return "下午"
    elif hour < 20:
        return "傍晚"
    else:
        return "夜晚"


# ============================================================
# Chat API
# ============================================================

@app.post("/api/chat/send")
async def chat_send(request: Request):
    body = await request.json()
    content = body.get("content", "")
    msg_type = body.get("type", "text")
    image_desc = body.get("image_desc", "")

    if not content and not image_desc:
        return JSONResponse({"error": "empty message"}, status_code=400)

    db.insert_message("user", content, msg_type, image_desc=image_desc)

    reply_data = await asyncio.to_thread(
        chat_service.chat_reply, content, image_desc=image_desc or None
    )

    triggered_id = ""
    if reply_data.get("trigger_video") and reply_data.get("video_desc"):
        desc = reply_data["video_desc"]
        def _gen():
            try:
                evt = scheduler.generate_single_event(desc)
                if evt:
                    print(f"  [聊天触发] 生成视频: {evt['title']}")
            except Exception as e:
                logger.error(f"[聊天触发] 视频生成失败: {e}", exc_info=True)
        threading.Thread(target=_gen, daemon=True).start()
        triggered_id = "generating"

    db.insert_message("tutu", reply_data["reply"], "text", triggered_video_id=triggered_id)

    return {
        "reply": reply_data["reply"],
        "trigger_video": reply_data.get("trigger_video", False),
        "video_desc": reply_data.get("video_desc", "")
    }


@app.get("/api/chat/history")
async def chat_history(before: str = None, limit: int = 20):
    messages = db.get_chat_history(before=before, limit=limit)
    return {"messages": messages}


# ============================================================
# 状态 API
# ============================================================

@app.get("/api/tutu/status")
async def tutu_status():
    today_events = db.get_today_events()
    now = datetime.now()

    current = "在家待着"
    mood = "平静"
    for e in today_events:
        evt_time = e.get("publish_at", "")
        if evt_time and evt_time <= now.isoformat():
            current = e["title"]
            if e.get("inner_voice"):
                mood = e["inner_voice"][:20]

    return {
        "current_activity": current,
        "mood": mood,
        "today_total": len(today_events),
        "today_ready": sum(1 for e in today_events if e.get("video_status") == "ready"),
    }


# ============================================================
# 管理 API（需要鉴权）
# ============================================================

@app.post("/api/admin/generate")
async def admin_generate(request: Request, x_api_key: str = Header(None)):
    if not _check_admin(x_api_key):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    date_str = body.get("date", datetime.now().strftime("%Y-%m-%d"))
    weather = body.get("weather", "")
    user_city = body.get("user_city", "")

    def _gen():
        try:
            scheduler.generate_daily(date_str=date_str, weather=weather, user_city=user_city)
        except Exception as e:
            logger.error(f"[admin] 生成失败: {e}", exc_info=True)
    threading.Thread(target=_gen, daemon=True).start()

    return {"status": "started", "date": date_str}


@app.get("/api/admin/events")
async def admin_events(x_api_key: str = Header(None)):
    if not _check_admin(x_api_key):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    with db.db_conn() as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY publish_at DESC LIMIT 50").fetchall()
    return {"events": [dict(r) for r in rows]}


# ============================================================
# 视频文件服务
# ============================================================

@app.get("/videos/{filename}")
async def serve_video(filename: str):
    # 安全校验：只允许纯文件名，防止路径遍历
    if "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse({"error": "invalid filename"}, status_code=400)

    fpath = (STATIC_VIDEOS / filename).resolve()
    if not fpath.is_relative_to(STATIC_VIDEOS.resolve()):
        return JSONResponse({"error": "invalid filename"}, status_code=400)

    if not fpath.exists():
        src = (VIDEOS_SRC / filename).resolve()
        if src.exists() and src.is_relative_to(VIDEOS_SRC.resolve()):
            fpath = src
        else:
            return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(fpath), media_type="video/mp4")


# ============================================================
# 启动
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
