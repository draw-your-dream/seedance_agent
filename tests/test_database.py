# -*- coding: utf-8 -*-
"""app.database 单元测试 — 使用临时DB"""

import json
import sys
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch

# 确保项目根目录在路径中
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """每个测试用独立的临时DB"""
    db_path = tmp_path / "test_tutu.db"
    monkeypatch.setattr("tutu_core.config.DB_PATH", db_path)
    # 由于 database.py 在模块级 import DB_PATH，需要重新 patch
    import database as db
    monkeypatch.setattr(db, "DB_PATH", db_path)
    # 清空连接池
    db._pool.clear()
    db.init_db()
    yield db
    db._pool.clear()


class TestInitDb:
    def test_tables_created(self, temp_db):
        conn = temp_db.get_conn()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t["name"] for t in tables}
        assert "events" in table_names
        assert "messages" in table_names
        temp_db.release_conn(conn)

    def test_new_columns_exist(self, temp_db):
        conn = temp_db.get_conn()
        cols = conn.execute("PRAGMA table_info(events)").fetchall()
        col_names = {c["name"] for c in cols}
        assert "thoughts" in col_names
        assert "weather" in col_names
        assert "is_new" in col_names
        temp_db.release_conn(conn)


class TestInsertAndGetEvent:
    def _make_event(self, **overrides):
        evt = {
            "id": "test_001", "date": "2026-04-10", "time": "09:00",
            "publish_at": "2026-04-10T09:00:00", "title": "测试事件",
            "summary": "测试摘要", "inner_voice": "嘟~",
            "thoughts": [{"time": "09:01", "text": "碎碎念"}],
            "weather": "☀️", "video_prompt": "", "video_url": "",
            "video_status": "ready", "task_id": "", "triggered_by": "daily",
            "user_related": 0, "is_new": 0,
        }
        evt.update(overrides)
        return evt

    def test_insert_and_retrieve(self, temp_db):
        temp_db.insert_event(self._make_event())
        events = temp_db.get_today_events("2026-04-10")
        assert len(events) == 1
        assert events[0]["title"] == "测试事件"

    def test_thoughts_serialization(self, temp_db):
        thoughts = [{"time": "09:01", "text": "第一条"}, {"time": "09:02", "text": "第二条"}]
        temp_db.insert_event(self._make_event(thoughts=thoughts))
        events = temp_db.get_today_events("2026-04-10")
        assert isinstance(events[0]["thoughts"], list)
        assert len(events[0]["thoughts"]) == 2
        assert events[0]["thoughts"][0]["text"] == "第一条"

    def test_empty_thoughts_auto_generates_from_inner_voice(self, temp_db):
        temp_db.insert_event(self._make_event(
            id="test_002", thoughts="", inner_voice="嘟~ 自动生成"
        ))
        events = temp_db.get_today_events("2026-04-10")
        e = [e for e in events if e["id"] == "test_002"][0]
        assert isinstance(e["thoughts"], list)
        assert len(e["thoughts"]) == 1
        assert "自动生成" in e["thoughts"][0]["text"]

    def test_weather_and_is_new(self, temp_db):
        temp_db.insert_event(self._make_event(weather="🌧️ 雨", is_new=1))
        events = temp_db.get_today_events("2026-04-10")
        assert events[0]["weather"] == "🌧️ 雨"
        assert events[0]["is_new"] == 1

    def test_upsert(self, temp_db):
        temp_db.insert_event(self._make_event(title="版本1"))
        temp_db.insert_event(self._make_event(title="版本2"))
        events = temp_db.get_today_events("2026-04-10")
        assert len(events) == 1
        assert events[0]["title"] == "版本2"


class TestGetFeed:
    def test_only_ready_events(self, temp_db):
        temp_db.insert_event({
            "id": "ready_1", "date": "2026-04-10", "time": "09:00",
            "publish_at": "2026-04-10T09:00:00", "title": "已就绪",
            "video_status": "ready",
        })
        temp_db.insert_event({
            "id": "pending_1", "date": "2026-04-10", "time": "10:00",
            "publish_at": "2026-04-10T10:00:00", "title": "等待中",
            "video_status": "generating",
        })
        feed = temp_db.get_feed(limit=10)
        assert all(e["video_status"] == "ready" for e in feed)


class TestMessages:
    def test_insert_and_history(self, temp_db):
        temp_db.insert_message("user", "你好啊")
        temp_db.insert_message("tutu", "嘟？")
        history = temp_db.get_chat_history(limit=10)
        assert len(history) == 2
        assert history[0]["sender"] == "user"
        assert history[1]["sender"] == "tutu"

    def test_recent_interactions(self, temp_db):
        temp_db.insert_message("user", "沙滩好玩")
        temp_db.insert_message("tutu", "嘟！")  # 非user消息应被过滤
        temp_db.insert_message("user", "今天好累", "text")
        result = temp_db.get_recent_interactions(10)
        assert "沙滩好玩" in result
        assert "今天好累" in result
        assert "嘟" not in result

    def test_empty_interactions(self, temp_db):
        result = temp_db.get_recent_interactions(10)
        assert "还没有" in result


class TestConnectionPool:
    def test_reuse_connection(self, temp_db):
        conn1 = temp_db.get_conn()
        temp_db.release_conn(conn1)
        conn2 = temp_db.get_conn()
        # 同一个对象从池中取出
        assert conn1 is conn2
        temp_db.release_conn(conn2)

    def test_pool_limit(self, temp_db):
        # 直接操作pool验证上限，不通过 get_conn 创建新连接
        import sqlite3
        for _ in range(6):
            c = sqlite3.connect(str(temp_db.DB_PATH), check_same_thread=False)
            c.row_factory = sqlite3.Row
            temp_db.release_conn(c)
        assert len(temp_db._pool) <= temp_db._POOL_SIZE


class TestGetEventsByDateRange:
    def test_range_query(self, temp_db):
        for d in ["2026-04-08", "2026-04-09", "2026-04-10"]:
            temp_db.insert_event({
                "id": f"evt_{d}", "date": d, "time": "09:00",
                "publish_at": f"{d}T09:00:00", "title": f"事件{d}",
            })
        events = temp_db.get_events_by_date_range("2026-04-09", "2026-04-10")
        dates = {e["date"] for e in events}
        assert "2026-04-08" not in dates
        assert "2026-04-09" in dates
        assert "2026-04-10" in dates
