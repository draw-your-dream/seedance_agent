# -*- coding: utf-8 -*-
"""
SQLite数据库层 — 单一事实源

统一数据模型：
  events表：所有事件（含 thoughts/weather/is_new 字段）
  messages表：所有用户互动（废弃 user_memory.json）
"""

import sqlite3
import json
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timedelta

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tutu_core.config import DB_PATH, VIDEO_DIR, LIFE_JOURNAL_FILE


# ============================================================
# 线程安全连接池
# ============================================================

_pool_lock = threading.Lock()
_pool: list[sqlite3.Connection] = []
_POOL_SIZE = 4


def get_conn() -> sqlite3.Connection:
    """从池中获取连接。池空时新建。"""
    with _pool_lock:
        if _pool:
            conn = _pool.pop()
            try:
                conn.execute("SELECT 1")
                return conn
            except (sqlite3.ProgrammingError, sqlite3.OperationalError):
                try:
                    conn.close()
                except Exception:
                    pass
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def release_conn(conn: sqlite3.Connection):
    """归还连接到池。池满则关闭。"""
    with _pool_lock:
        if len(_pool) < _POOL_SIZE:
            _pool.append(conn)
        else:
            try:
                conn.close()
            except Exception:
                pass


@contextmanager
def db_conn():
    """上下文管理器：自动获取和归还连接，异常时也能安全释放。"""
    conn = get_conn()
    try:
        yield conn
    finally:
        release_conn(conn)


def init_db():
    with db_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            publish_at TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT,
            inner_voice TEXT,
            thoughts TEXT,
            weather TEXT DEFAULT '',
            video_prompt TEXT,
            video_url TEXT,
            video_status TEXT DEFAULT 'pending',
            task_id TEXT,
            triggered_by TEXT DEFAULT 'daily',
            user_related INTEGER DEFAULT 0,
            is_new INTEGER DEFAULT 0,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            sender TEXT NOT NULL,
            msg_type TEXT DEFAULT 'text',
            content TEXT,
            image_path TEXT,
            image_desc TEXT,
            triggered_video_id TEXT,
            created_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_events_publish ON events(publish_at);
        CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);
        CREATE INDEX IF NOT EXISTS idx_events_status ON events(video_status);
        CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
        """)
        # 迁移：为已有DB添加新字段（ALTER TABLE IF NOT EXISTS 不支持，用 try）
        for col, default in [("thoughts", ""), ("weather", ""), ("is_new", 0), ("retry_count", 0)]:
            try:
                conn.execute(f"ALTER TABLE events ADD COLUMN {col} {'TEXT' if isinstance(default, str) else 'INTEGER'} DEFAULT ?", (default,))
            except sqlite3.OperationalError:
                pass  # 字段已存在
        conn.commit()


# ============================================================
# Events CRUD
# ============================================================

def insert_event(evt):
    # thoughts 如果是 list/dict 就序列化为 JSON 字符串
    thoughts = evt.get("thoughts", "")
    if isinstance(thoughts, (list, dict)):
        thoughts = json.dumps(thoughts, ensure_ascii=False)

    with db_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO events
            (id, date, time, publish_at, title, summary, inner_voice, thoughts, weather,
             video_prompt, video_url, video_status, task_id, triggered_by, user_related, is_new, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            evt["id"], evt["date"], evt["time"], evt["publish_at"],
            evt["title"], evt.get("summary", ""), evt.get("inner_voice", ""),
            thoughts, evt.get("weather", ""),
            evt.get("video_prompt", ""), evt.get("video_url", ""),
            evt.get("video_status", "pending"), evt.get("task_id", ""),
            evt.get("triggered_by", "daily"), evt.get("user_related", 0),
            evt.get("is_new", 0),
            datetime.now().isoformat()
        ))
        conn.commit()


def _row_to_event(row):
    """将DB行转为dict，自动反序列化 thoughts JSON"""
    d = dict(row)
    # 反序列化 thoughts
    raw = d.get("thoughts", "")
    if raw and isinstance(raw, str):
        try:
            d["thoughts"] = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            # 纯文本 inner_voice 风格，转为单条 thought
            if raw.strip():
                d["thoughts"] = [{"time": d.get("time", ""), "text": raw}]
            else:
                d["thoughts"] = []
    elif not raw:
        # 没有 thoughts 但有 inner_voice 时，自动生成
        voice = d.get("inner_voice", "")
        if voice:
            d["thoughts"] = [{"time": d.get("time", ""), "text": voice}]
        else:
            d["thoughts"] = []
    return d


def get_feed(before=None, limit=10):
    """获取已发布的视频流（publish_at <= now 且视频ready）"""
    with db_conn() as conn:
        now = datetime.now().isoformat()
        if before:
            rows = conn.execute("""
                SELECT * FROM events
                WHERE publish_at <= ? AND publish_at < ? AND video_status = 'ready'
                ORDER BY publish_at DESC LIMIT ?
            """, (now, before, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM events
                WHERE publish_at <= ? AND video_status = 'ready'
                ORDER BY publish_at DESC LIMIT ?
            """, (now, limit)).fetchall()
    return [_row_to_event(r) for r in rows]


def get_today_events(date_str=None):
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE date = ? ORDER BY time", (date_str,)
        ).fetchall()
    return [_row_to_event(r) for r in rows]


def get_events_by_date_range(start_date, end_date=None, limit=50):
    """获取日期范围内的事件（供timeline API使用）"""
    with db_conn() as conn:
        if end_date:
            rows = conn.execute(
                "SELECT * FROM events WHERE date >= ? AND date <= ? ORDER BY date DESC, time ASC LIMIT ?",
                (start_date, end_date, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events WHERE date >= ? ORDER BY date DESC, time ASC LIMIT ?",
                (start_date, limit)
            ).fetchall()
    return [_row_to_event(r) for r in rows]


def update_event_video(event_id, video_url=None, video_status=None, task_id=None):
    updates = []
    params = []
    if video_url is not None:
        updates.append("video_url = ?")
        params.append(video_url)
    if video_status is not None:
        updates.append("video_status = ?")
        params.append(video_status)
    if task_id is not None:
        updates.append("task_id = ?")
        params.append(task_id)
    if updates:
        with db_conn() as conn:
            params.append(event_id)
            conn.execute(f"UPDATE events SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()


# ============================================================
# Messages CRUD（单一事实源，废弃 user_memory.json）
# ============================================================

def insert_message(sender, content, msg_type="text", image_path="", image_desc="", triggered_video_id=""):
    ts = datetime.now().isoformat()
    with db_conn() as conn:
        conn.execute("""
            INSERT INTO messages (timestamp, sender, msg_type, content, image_path, image_desc, triggered_video_id, created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (ts, sender, msg_type, content, image_path, image_desc, triggered_video_id, ts))
        conn.commit()
    return ts


def get_chat_history(before=None, limit=20):
    with db_conn() as conn:
        if before:
            rows = conn.execute(
                "SELECT * FROM messages WHERE timestamp < ? ORDER BY timestamp DESC LIMIT ?",
                (before, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM messages ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
    return list(reversed([dict(r) for r in rows]))


def get_recent_interactions(limit=20):
    """获取最近用户互动（供LLM上下文用）"""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE sender = 'user' ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        ).fetchall()
    lines = []
    for r in reversed(rows):
        r = dict(r)
        if r["msg_type"] == "image" and r.get("image_desc"):
            lines.append(f"[{r['timestamp'][:16]}] 用户发了图片：{r['image_desc']}")
        else:
            lines.append(f"[{r['timestamp'][:16]}] 用户说：{r['content']}")
    return "\n".join(lines) if lines else "（用户还没有跟秃秃互动过）"


def get_recent_journal(days=7):
    """获取最近N天的事件摘要（供LLM上下文用）"""
    with db_conn() as conn:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT date, time, title, summary FROM events WHERE date >= ? ORDER BY publish_at DESC LIMIT 15",
            (cutoff,)
        ).fetchall()
    if not rows:
        return "（秃秃刚开始记录生活，还没有历史）"
    return "\n".join(f"[{r['date']} {r['time']}] {r['title']}: {r['summary']}" for r in reversed(rows))


# ============================================================
# Seed：导入历史数据（含 thoughts 生成）
# ============================================================

# 历史视频对应的 thoughts（来自 timeline_data.json，之前只在前端展示）
_SEED_THOUGHTS = {
    "秃秃吃提拉米苏": [
        {"time": "14:03", "text": "这个褐色粉粉是什么"},
        {"time": "14:02", "text": "嘴里有点苦又有点甜"},
        {"time": "14:01", "text": "人类食物好复杂"},
    ],
    "秃秃第一次认识闹钟": [
        {"time": "20:05", "text": "这个圆盘会叫！"},
        {"time": "20:03", "text": "明天它会叫我起床吗"},
    ],
    "秃秃vs电动牙刷": [
        {"time": "10:03", "text": "这个东西会动会抖！好可怕！"},
    ],
    "秃秃奶茶店员": [
        {"time": "18:33", "text": "欢迎光临嘟嘟奶茶"},
        {"time": "18:31", "text": "不知道奶茶什么味道，看起来好忙"},
    ],
    "秃秃给你做早餐": [
        {"time": "08:05", "text": "今天给小薯条做早餐！"},
        {"time": "08:02", "text": "虽然我只会做草……草也很好吃的"},
    ],
    "秃秃赖床": [
        {"time": "07:35", "text": "再躺五分钟……被子好暖"},
        {"time": "07:32", "text": "手太短够不到闹钟"},
    ],
    "秃秃坐纽扣滑板": [
        {"time": "10:33", "text": "滑起来了！好快好快！"},
    ],
    "秃秃水龙头洗澡": [
        {"time": "07:33", "text": "水好大差点被冲走"},
    ],
    "秃秃和瓢虫晒太阳": [
        {"time": "10:05", "text": "它帽子颜色跟我好像，我们是不是亲戚"},
    ],
    "秃秃钻进袜子睡觉": [
        {"time": "21:03", "text": "软软的像睡袋"},
        {"time": "21:01", "text": "明天也要元气满满"},
    ],
    "秃秃荡秋千": [
        {"time": "18:03", "text": "荡到最高能看好远的地方"},
    ],
    "秃秃踩水坑": [
        {"time": "16:03", "text": "噗叽！水花溅脸上了哈哈哈"},
    ],
    "秃秃被风吹": [
        {"time": "17:03", "text": "站不住了！帽子要飞了！"},
    ],
    "秃秃照镜子": [
        {"time": "07:05", "text": "里面也有一个我"},
        {"time": "07:02", "text": "嘿嘿好可爱（是在说我自己）"},
    ],
    "秃秃搬草莓": [
        {"time": "14:05", "text": "比我还大！闻起来好甜"},
        {"time": "14:02", "text": "但是我搬不动"},
    ],
}


def seed_existing_videos():
    """将现有视频导入数据库，让app启动就有内容"""
    with db_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM events WHERE video_status = 'ready'").fetchone()[0]
        if count > 0:
            return

        # 从life_journal读取
        journal = []
        if LIFE_JOURNAL_FILE.exists():
            with open(LIFE_JOURNAL_FILE, "r", encoding="utf-8") as f:
                journal = json.load(f)

        # 扫描视频文件
        video_files = {}
        if VIDEO_DIR.exists():
            for vf in VIDEO_DIR.glob("*.mp4"):
                video_files[vf.name] = vf

        # 导入journal中的条目
        imported = 0
        for entry in journal:
            date_str = entry.get("date", "2026-04-08")
            time_str = entry.get("time", "12:00")
            title = entry.get("title", "")
            if not title:
                continue

            video_url = ""
            clean_title = title.replace("/", "_").replace(" ", "_")
            for vname in video_files:
                if clean_title in vname or title in vname:
                    video_url = f"/videos/{vname}"
                    break

            # 生成 thoughts：从 inner_voice 自动拆分
            inner_voice = entry.get("inner_voice", "")
            thoughts = [{"time": time_str, "text": inner_voice}] if inner_voice else []

            evt_id = f"evt_{date_str}_{time_str.replace(':', '')}_{imported}"
            publish_at = f"{date_str}T{time_str}:00"

            conn.execute("""
                INSERT OR IGNORE INTO events
                (id, date, time, publish_at, title, summary, inner_voice, thoughts, weather,
                 video_url, video_status, task_id, triggered_by, user_related, is_new, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                evt_id, date_str, time_str, publish_at, title,
                entry.get("summary", ""), inner_voice,
                json.dumps(thoughts, ensure_ascii=False), "",
                video_url, "ready" if video_url else "pending",
                entry.get("task_id", ""), entry.get("triggered_by", "daily"),
                1 if entry.get("user_related") else 0,
                0,
                datetime.now().isoformat()
            ))
            imported += 1

        # 导入output/videos中的历史视频（batch 1-4，含 thoughts 数据）
        existing_videos = [
            ("2026-04-08", "08:00", "秃秃吃提拉米苏", "01_秃秃吃提拉米苏.mp4"),
            ("2026-04-08", "09:00", "秃秃第一次认识闹钟", "02_秃秃第一次认识闹钟.mp4"),
            ("2026-04-08", "10:00", "秃秃vs电动牙刷", "03_秃秃vs电动牙刷.mp4"),
            ("2026-04-08", "11:00", "秃秃奶茶店员", "04_秃秃奶茶店员.mp4"),
            ("2026-04-08", "12:00", "秃秃给你做早餐", "05_秃秃给你做早餐.mp4"),
            ("2026-04-08", "13:00", "秃秃赖床", "06_秃秃赖床.mp4"),
            ("2026-04-08", "14:00", "秃秃坐纽扣滑板", "07_秃秃坐纽扣滑板.mp4"),
            ("2026-04-08", "15:00", "秃秃水龙头洗澡", "08_秃秃水龙头洗澡.mp4"),
            ("2026-04-08", "16:00", "秃秃和瓢虫晒太阳", "09_秃秃和瓢虫晒太阳.mp4"),
            ("2026-04-08", "17:00", "秃秃钻进袜子睡觉", "10_秃秃钻进袜子睡觉.mp4"),
            ("2026-04-08", "18:00", "秃秃荡秋千", "11_秃秃荡秋千.mp4"),
            ("2026-04-08", "19:00", "秃秃踩水坑", "12_秃秃踩水坑.mp4"),
            ("2026-04-08", "20:00", "秃秃被风吹", "13_秃秃被风吹.mp4"),
            ("2026-04-08", "20:30", "秃秃照镜子", "14_秃秃照镜子.mp4"),
            ("2026-04-08", "21:00", "秃秃搬草莓", "15_秃秃搬草莓.mp4"),
        ]
        for date_str, time_str, title, filename in existing_videos:
            if filename in video_files:
                evt_id = f"seed_{filename.split('_')[0]}"
                publish_at = f"{date_str}T{time_str}:00"
                thoughts = _SEED_THOUGHTS.get(title, [])
                conn.execute("""
                    INSERT OR IGNORE INTO events
                    (id, date, time, publish_at, title, summary, inner_voice, thoughts, weather,
                     video_url, video_status, triggered_by, user_related, is_new, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    evt_id, date_str, time_str, publish_at, title,
                    "", "",
                    json.dumps(thoughts, ensure_ascii=False), "",
                    f"/videos/{filename}", "ready", "daily", 0, 0,
                    datetime.now().isoformat()
                ))

        conn.commit()
        print(f"  导入 {imported} 条journal + {len(existing_videos)} 条历史视频")
