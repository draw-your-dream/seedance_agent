# -*- coding: utf-8 -*-
"""
轮询 Batch 03 Seedance 任务状态，完成后下载视频（已重构：使用 tutu_core）
"""
import json
import sys
import time
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tutu_core.config import VIDEO_DIR
from tutu_core.seedance_client import query_task, download_video

TASK_FILE = "/tmp/batch03_tasks.json"
POLL_INTERVAL = 30  # 秒（原来20秒太短，容易被限速）
MAX_POLLS = 60      # 最大轮询次数（约30分钟），防止无限循环


def main():
    task_path = Path(TASK_FILE)
    if not task_path.exists():
        print(f"❌ 任务文件不存在: {TASK_FILE}")
        sys.exit(1)

    with open(TASK_FILE, "r") as f:
        tasks = json.load(f)

    tasks = [t for t in tasks if t.get("task_id")]
    if not tasks:
        print("❌ 没有可查询的任务")
        sys.exit(1)

    VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    print(f"轮询 {len(tasks)} 个任务，间隔 {POLL_INTERVAL}s，最多 {MAX_POLLS} 轮")
    print(f"视频保存到: {VIDEO_DIR}\n")

    completed = set()
    poll_count = 0

    while len(completed) < len(tasks) and poll_count < MAX_POLLS:
        poll_count += 1
        for t in tasks:
            tid = t["task_id"]
            if tid in completed:
                continue

            resp = query_task(tid)
            status = resp.get("status", "unknown")

            if status == "succeeded":
                print(f"✅ #{t['num']:02d} {t['title']} — 生成成功!")
                # 提取视频URL
                content = resp.get("content", {})
                video_url = None
                if isinstance(content, dict):
                    video_url = content.get("video_url") or content.get("url")
                    if not video_url and "videos" in content:
                        videos = content["videos"]
                        if isinstance(videos, list) and videos:
                            video_url = videos[0].get("url")
                if not video_url:
                    video_url = resp.get("video_url") or resp.get("url")
                if not video_url and "output" in resp:
                    out = resp["output"]
                    if isinstance(out, dict):
                        video_url = out.get("video_url") or out.get("url")
                    elif isinstance(out, str):
                        video_url = out

                if video_url:
                    filename = f"{t['num']:02d}_{t['title']}.mp4"
                    ok, info = download_video(video_url, VIDEO_DIR / filename)
                    print(f"   {'✅' if ok else '❌'} {info}")
                else:
                    print(f"   ⚠️ 找不到视频URL")
                    print(f"   {json.dumps(resp, ensure_ascii=False)[:300]}")
                completed.add(tid)

            elif status in ("failed", "cancelled"):
                err = resp.get("error", {})
                print(f"❌ #{t['num']:02d} {t['title']} — {status}: {err}")
                completed.add(tid)
            else:
                print(f"   ⏳ #{t['num']:02d} {t['title']} — {status}")

        if len(completed) < len(tasks):
            time.sleep(POLL_INTERVAL)

    if len(completed) < len(tasks):
        remaining = len(tasks) - len(completed)
        print(f"\n⚠️ 达到最大轮询次数({MAX_POLLS})，仍有 {remaining} 个任务未完成")
    else:
        print(f"\n{'=' * 50}")
        print(f"全部完成! 视频在: {VIDEO_DIR}")
        print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
