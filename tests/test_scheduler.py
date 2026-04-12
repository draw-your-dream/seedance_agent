# -*- coding: utf-8 -*-
"""scheduler.py 单元测试"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))


class TestPollAndDownload:
    """poll_and_download 重试逻辑测试"""

    @patch("scheduler.db")
    @patch("scheduler.query_task")
    @patch("scheduler.download_video")
    def test_download_succeeded(self, mock_dl, mock_query, mock_db):
        """成功的任务应下载并标记 ready"""
        import scheduler

        mock_row = {
            "id": "evt_001", "task_id": "task_abc", "title": "秃秃赖床",
            "video_status": "generating", "video_prompt": "", "retry_count": 0,
        }

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [mock_row]
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("database.db_conn", return_value=mock_conn):
            mock_query.return_value = {"status": "succeeded", "content": {"video_url": "https://example.com/v.mp4"}}
            mock_dl.return_value = (True, "2.5MB")

            scheduler.poll_and_download()

            mock_db.update_event_video.assert_called_once()

    @patch("scheduler.db")
    @patch("scheduler.query_task")
    def test_failed_task_marked(self, mock_query, mock_db):
        """失败的 generating 任务应标记为 failed"""
        import scheduler

        mock_row = {
            "id": "evt_002", "task_id": "task_def", "title": "秃秃踩水坑",
            "video_status": "generating", "video_prompt": "", "retry_count": 0,
        }

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [mock_row]
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("database.db_conn", return_value=mock_conn):
            mock_query.return_value = {"status": "failed"}
            scheduler.poll_and_download()
            mock_db.update_event_video.assert_called_once()

    @patch("scheduler.db")
    @patch("scheduler.submit_task")
    @patch("scheduler.load_reference_image")
    def test_retry_failed_task(self, mock_img, mock_submit, mock_db):
        """失败任务 retry_count < MAX 应重新提交"""
        import scheduler

        mock_row = {
            "id": "evt_003", "task_id": "task_old", "title": "秃秃照镜子",
            "video_status": "failed", "video_prompt": "图片1是小蘑菇角色形象参考。" + "x" * 400,
            "retry_count": 1,
        }

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [mock_row]
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("database.db_conn", return_value=mock_conn):
            mock_img.return_value = "base64img"
            mock_submit.return_value = ("new_task_id", None)

            scheduler.poll_and_download()

            mock_submit.assert_called_once()

    def test_no_rows_returns_early(self):
        """没有待处理任务时应直接返回"""
        import scheduler

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("database.db_conn", return_value=mock_conn):
            scheduler.poll_and_download()  # 不抛异常即通过


class TestGenerateSingleEvent:
    """generate_single_event 测试"""

    @patch("scheduler.db")
    @patch("scheduler.submit_task")
    @patch("scheduler.load_reference_image")
    @patch("scheduler.generate_event_content")
    def test_returns_event_data(self, mock_gen, mock_img, mock_submit, mock_db):
        """成功生成时返回完整事件数据"""
        import scheduler

        mock_gen.return_value = {
            "video_prompt": "图片1是小蘑菇角色形象参考。测试prompt内容",
            "inner_voice": "嘟嘟嘟",
            "thoughts": [{"time": "14:00", "text": "好奇"}],
        }
        mock_img.return_value = "base64img"
        mock_submit.return_value = ("task_123", None)

        result = scheduler.generate_single_event("秃秃看夕阳", date_str="2026-04-11")
        assert result is not None
        assert result["triggered_by"] == "user"
        assert result["video_status"] == "generating"
        assert result["task_id"] == "task_123"
        mock_db.insert_event.assert_called_once()

    @patch("scheduler.db")
    @patch("scheduler.generate_event_content")
    def test_returns_none_on_content_failure(self, mock_gen, mock_db):
        """内容生成失败时返回 None"""
        import scheduler

        mock_gen.return_value = None
        result = scheduler.generate_single_event("秃秃看夕阳")
        assert result is None
        mock_db.insert_event.assert_not_called()
