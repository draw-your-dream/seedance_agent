# -*- coding: utf-8 -*-
"""server.py API 端点测试（httpx 0.28 + FastAPI 0.104 兼容）"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import pytest
import httpx
import pytest_asyncio

# 确保 static 目录存在（FastAPI StaticFiles 需要）
_static_dir = Path(__file__).resolve().parent.parent / "app" / "static"
_static_dir.mkdir(parents=True, exist_ok=True)
(_static_dir / "videos").mkdir(exist_ok=True)
(_static_dir / "uploads").mkdir(exist_ok=True)

from server import app

_transport = httpx.ASGITransport(app=app)


async def _get(path, **kw):
    async with httpx.AsyncClient(transport=_transport, base_url="http://test") as c:
        return await c.get(path, **kw)


async def _post(path, **kw):
    async with httpx.AsyncClient(transport=_transport, base_url="http://test") as c:
        return await c.post(path, **kw)


@pytest.mark.asyncio
class TestIndexPage:
    async def test_root_returns_html(self):
        resp = await _get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


@pytest.mark.asyncio
class TestFeedAPI:
    @patch("server.db")
    async def test_feed_returns_events(self, mock_db):
        mock_db.get_feed.return_value = [
            {"id": "e1", "video_url": "/videos/test.mp4", "thoughts": [],
             "inner_voice": "", "time": "10:00"}
        ]
        resp = await _get("/api/feed")
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data
        assert len(data["events"]) == 1

    @patch("server.db")
    async def test_feed_empty(self, mock_db):
        mock_db.get_feed.return_value = []
        resp = await _get("/api/feed")
        assert resp.status_code == 200
        assert resp.json()["has_more"] is False

    @patch("server.db")
    async def test_today_feed(self, mock_db):
        mock_db.get_today_events.return_value = []
        resp = await _get("/api/feed/today")
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestChatAPI:
    async def test_empty_message_rejected(self):
        resp = await _post("/api/chat/send", json={"content": "", "type": "text"})
        assert resp.status_code == 400

    @patch("server.db")
    async def test_chat_history(self, mock_db):
        mock_db.get_chat_history.return_value = [
            {"sender": "user", "content": "hi", "timestamp": "2026-04-11T10:00:00"}
        ]
        resp = await _get("/api/chat/history")
        assert resp.status_code == 200
        assert "messages" in resp.json()


@pytest.mark.asyncio
class TestStatusAPI:
    @patch("server.db")
    async def test_status_returns_fields(self, mock_db):
        mock_db.get_today_events.return_value = []
        resp = await _get("/api/tutu/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "current_activity" in data
        assert "mood" in data
        assert "today_total" in data


@pytest.mark.asyncio
class TestVideoServing:
    async def test_path_traversal_blocked(self):
        """路径遍历攻击应被拦截"""
        resp = await _get("/videos/..%2F..%2F.env")
        assert resp.status_code in (400, 404)

    async def test_nonexistent_video_returns_404(self):
        resp = await _get("/videos/nonexistent_12345.mp4")
        assert resp.status_code == 404
