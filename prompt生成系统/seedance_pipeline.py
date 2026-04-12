#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
秃秃IP Seedance视频生成 — 完整自动化Pipeline

沉淀自Batch 1-4的所有经验教训，一个脚本搞定：
  提取prompt → 质量校验 → 生成payload → 回读验证 → 提交API → 下载视频

使用方式:
  python seedance_pipeline.py run --input batch05_待确认.md
  python seedance_pipeline.py check --input batch05_待确认.md
  python seedance_pipeline.py download --tasks /tmp/batchXX_tasks.json
  python seedance_pipeline.py run --input batch05_待确认.md --only 2,5
"""

import argparse
import json
import re
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tutu_core.config import (
    OUTPUT_DIR, VIDEO_DIR, SEEDANCE_CONCURRENCY, SEEDANCE_DURATION,
)
from tutu_core.markdown_parser import extract_prompts
from tutu_core.validators import validate_prompt
from tutu_core.seedance_client import (
    load_reference_image, submit_task, query_task, download_video,
)

SCRIPT_DIR = Path(__file__).parent


# ============================================================
# Step 4: 批量提交（保留原有的分批逻辑）
# ============================================================

def submit_batch(prompts, img_b64, concurrency=SEEDANCE_CONCURRENCY, duration=SEEDANCE_DURATION):
    """按并发数分批提交"""
    results = []
    for i in range(0, len(prompts), concurrency):
        batch = prompts[i:i + concurrency]
        for p in batch:
            task_id, error = submit_task(
                p["text"], img_b64, duration,
                payload_tag=f"{p['num']:02d}"
            )
            if task_id:
                print(f"  ✅ #{p['num']:02d} {p['title']} -> {task_id}")
                results.append({"num": p["num"], "title": p["title"], "task_id": task_id, "status": "submitted"})
            else:
                print(f"  ❌ #{p['num']:02d} {p['title']} -> {error}")
                results.append({"num": p["num"], "title": p["title"], "task_id": None, "status": "error", "error": error})
    return results


# ============================================================
# Step 5: 下载
# ============================================================

def make_video_filename(num, title):
    """生成视频文件名"""
    clean = re.sub(r'^(清晨|上午|中午|下午|夜晚)\s*·\s*', '', title)
    clean = clean.replace("/", "_").replace(" ", "_")
    return f"{num:02d}_{clean}.mp4"


def download_results(tasks_file):
    """从tasks json文件批量下载完成的视频"""
    with open(tasks_file, "r", encoding="utf-8") as f:
        tasks = json.load(f)

    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    for t in tasks:
        if not t.get("task_id"):
            print(f"  ⏭ #{t['num']:02d} {t['title']}: 无task_id，跳过")
            continue

        d = query_task(t["task_id"])
        status = d.get("status", "unknown")

        if status == "succeeded":
            url = d["content"]["video_url"]
            fname = make_video_filename(t["num"], t["title"])
            fpath = VIDEO_DIR / fname
            ok, info = download_video(url, fpath)
            print(f"  {'✅' if ok else '❌'} #{t['num']:02d} {t['title']}: {info} -> {fname}")
        elif status == "running":
            print(f"  ⏳ #{t['num']:02d} {t['title']}: 还在生成中")
        else:
            print(f"  ❌ #{t['num']:02d} {t['title']}: {status}")


# ============================================================
# 主命令: run
# ============================================================

def cmd_run(args):
    input_file = Path(args.input)
    if not input_file.is_absolute() and not input_file.exists():
        input_file = OUTPUT_DIR / input_file
    if not input_file.exists():
        print(f"❌ 文件不存在: {input_file}")
        sys.exit(1)

    only_nums = None
    if args.only:
        only_nums = set(int(x) for x in args.only.split(","))

    # Step 1: 提取
    print(f"\n{'='*60}")
    print(f"Step 1: 提取prompt")
    print(f"{'='*60}")
    prompts = extract_prompts(str(input_file))
    if only_nums:
        prompts = [p for p in prompts if p["num"] in only_nums]
    print(f"  提取到 {len(prompts)} 条prompt")

    if not prompts:
        print("  ❌ 没有提取到任何prompt")
        sys.exit(1)

    # Step 2: 校验
    print(f"\n{'='*60}")
    print(f"Step 2: 质量校验")
    print(f"{'='*60}")
    has_errors = False
    for p in prompts:
        passed, issues = validate_prompt(p["text"])
        status = "✅" if passed else "❌"
        print(f"  {status} #{p['num']:02d} {p['title']} ({len(p['text'])}字)")
        for issue in issues:
            print(f"     {issue}")
        if not passed:
            has_errors = True

    if has_errors:
        print(f"\n  ❌ 存在不通过项，修复后重新运行")
        sys.exit(1)

    # Step 3: 加载参考图片
    print(f"\n{'='*60}")
    print(f"Step 3: 加载参考图片")
    print(f"{'='*60}")
    img_b64 = load_reference_image()

    # Step 4: 提交
    print(f"\n{'='*60}")
    print(f"Step 4: 提交Seedance API（{SEEDANCE_CONCURRENCY}并发）")
    print(f"{'='*60}")
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    duration = args.duration or SEEDANCE_DURATION
    results = submit_batch(prompts, img_b64, duration=duration)

    # 保存结果
    success = sum(1 for r in results if r.get("task_id"))
    batch_name = input_file.stem.replace("_待确认", "")
    tasks_file = f"/tmp/{batch_name}_tasks.json"
    with open(tasks_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n  {success}/{len(results)} 提交成功")
    print(f"  任务ID保存到: {tasks_file}")
    print(f"\n  视频生成需要3-8分钟，完成后运行:")
    print(f"  python seedance_pipeline.py download --tasks {tasks_file}")


# ============================================================
# 主命令: check
# ============================================================

def cmd_check(args):
    input_file = Path(args.input)
    if not input_file.is_absolute() and not input_file.exists():
        input_file = OUTPUT_DIR / input_file
    if not input_file.exists():
        print(f"❌ 文件不存在: {input_file}")
        sys.exit(1)

    prompts = extract_prompts(str(input_file))
    print(f"提取到 {len(prompts)} 条prompt\n")

    passed_count = 0
    for p in prompts:
        passed, issues = validate_prompt(p["text"])
        status = "✅" if passed else "❌"
        print(f"{status} #{p['num']:02d} {p['title']} ({len(p['text'])}字)")
        for issue in issues:
            print(f"   {issue}")
        if passed:
            passed_count += 1
        print()

    print(f"{'='*40}")
    print(f"{passed_count}/{len(prompts)} 通过校验")


# ============================================================
# 主命令: download
# ============================================================

def cmd_download(args):
    tasks_file = Path(args.tasks)
    if not tasks_file.exists():
        print(f"❌ 文件不存在: {tasks_file}")
        sys.exit(1)

    print(f"从 {tasks_file} 下载视频...\n")
    download_results(str(tasks_file))
    print(f"\n视频目录: {VIDEO_DIR}")


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="秃秃IP Seedance视频生成Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python seedance_pipeline.py run --input batch05_待确认.md
  python seedance_pipeline.py check --input batch05_待确认.md
  python seedance_pipeline.py download --tasks /tmp/batch05_tasks.json
  python seedance_pipeline.py run --input batch05_待确认.md --only 21,23
        """
    )
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="完整流程: 校验→提交")
    p_run.add_argument("--input", required=True, help="prompt md文件路径")
    p_run.add_argument("--only", help="只处理指定编号，逗号分隔")
    p_run.add_argument("--duration", type=int, help=f"视频时长秒数（默认{SEEDANCE_DURATION}）")

    p_check = sub.add_parser("check", help="只做质量校验")
    p_check.add_argument("--input", required=True, help="prompt md文件路径")

    p_dl = sub.add_parser("download", help="下载已完成的视频")
    p_dl.add_argument("--tasks", required=True, help="tasks json文件路径")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    {"run": cmd_run, "check": cmd_check, "download": cmd_download}[args.command](args)


if __name__ == "__main__":
    main()
