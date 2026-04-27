"""通宵批跑：30 轮 × 50 条 = 1500 条全流程。

每轮独立的 Phase A → B → C → D，所有视频累积到同一个 videos 目录。
不同 run_label 和 seed 保证 context_id 不冲突且场景/IP 角色多样。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PIPE_DIR = Path(__file__).resolve().parent
LOG = PIPE_DIR / "overnight_v2.log"

ROUNDS = 30
PER_ROUND = 50
START_SEED = 20260501
LABEL_PREFIX = "ov"  # ov01 - ov30
START_ROUND = int(os.environ.get("START_ROUND", "1"))  # 1-indexed; 跳过已完成的轮次

VIDEOS_DIR = PIPE_DIR / "outputs" / "multi_sku_t2v_videos" / "videos"
PHASE_C_FULL = PIPE_DIR / "outputs" / "multi_sku_t2v_prompts" / "phase_c_multi_sku_t2v_prompts.jsonl"
TASKS_JSONL = PIPE_DIR / "outputs" / "multi_sku_t2v_videos" / "multi_sku_t2v_tasks.jsonl"

env = os.environ.copy()
if not env.get("CLAUDE_JWT_TOKEN"):
    raise RuntimeError("请先设置环境变量 CLAUDE_JWT_TOKEN")
if not env.get("ARK_API_KEY"):
    raise RuntimeError("请先设置环境变量 ARK_API_KEY")
env.setdefault("PYTHONIOENCODING", "utf-8")
env.setdefault("PHASE_A_WORKERS", "10")
env.setdefault("PHASE_C_WORKERS", "10")


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_step(args: list[str], cwd: Path = PIPE_DIR) -> int:
    log(f"  $ {' '.join(args)}")
    proc = subprocess.run(args, cwd=cwd, env=env)
    return proc.returncode


def filter_phase_c_for_label(label: str) -> Path:
    """从全量 phase_c jsonl 里抽出本 label 的条目，写到独立文件给 Phase D 用。"""
    dst = PIPE_DIR / "outputs" / "multi_sku_t2v_prompts" / f"phase_c_{label}.jsonl"
    cnt = 0
    with PHASE_C_FULL.open(encoding="utf-8-sig") as f, dst.open("w", encoding="utf-8") as g:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if label in rec.get("context_id", ""):
                g.write(json.dumps(rec, ensure_ascii=False) + "\n")
                cnt += 1
    log(f"  filtered {cnt} records → {dst.name}")
    return dst


def latest_phase_a_dir(label: str) -> Path | None:
    base = PIPE_DIR / "outputs" / "phase_a"
    cand = sorted(base.glob(f"phase_a_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in cand:
        jf = p / "phase_a_contexts.jsonl"
        if not jf.exists():
            continue
        first = jf.read_text(encoding="utf-8-sig").splitlines()[0]
        if label in first:
            return p
    return None


def main() -> None:
    log(f"==== 启动 {ROUNDS} 轮 × {PER_ROUND} 条 = {ROUNDS * PER_ROUND} 条（从 ROUND {START_ROUND} 开始）====")
    start_t = time.time()
    for r in range(START_ROUND - 1, ROUNDS):
        round_no = r + 1
        label = f"{LABEL_PREFIX}{round_no:02d}"
        seed = START_SEED + r * 13
        log(f"---- ROUND {round_no}/{ROUNDS} label={label} seed={seed} ----")

        # Phase A
        rc = run_step([
            sys.executable, "-u", "phase_a.py",
            "--run-label", label,
            "--count", str(PER_ROUND),
            "--batch-size", "1",
            "--seed", str(seed),
        ])
        if rc != 0:
            log(f"  [WARN] phase_a exit={rc}, skipping round")
            continue

        pa_dir = latest_phase_a_dir(label)
        if not pa_dir:
            log(f"  [WARN] phase_a output dir for {label} not found")
            continue
        pa_jsonl = pa_dir / "phase_a_contexts.jsonl"

        # Phase B (deterministic)
        rc = run_step([
            sys.executable, "-u", "phase_b_multi_sku_blueprints.py",
            "--contexts-jsonl", str(pa_jsonl),
        ])
        if rc != 0:
            log(f"  [WARN] phase_b exit={rc}, skipping round")
            continue

        # Phase C (parallel via Sonnet) — 全量 phase_c jsonl 共用，但各 round 的 context_id 独立
        bp_jsonl = PIPE_DIR / "outputs" / "multi_sku_blueprints" / "phase_b_multi_sku_blueprints.jsonl"
        rc = run_step([
            sys.executable, "-u", "phase_c_multi_sku_t2v_prompts.py",
            "--blueprints-jsonl", str(bp_jsonl),
            "--limit", "0",
        ])
        if rc != 0:
            log(f"  [WARN] phase_c exit={rc}, skipping round")
            continue

        # Phase D — 抽出本轮的 phase_c 条目单独跑
        round_pc = filter_phase_c_for_label(label)
        rc = run_step([
            sys.executable, "-u", "phase_d_parallel.py",
            "--prompts-jsonl", str(round_pc),
            "--limit", "0",
            "--workers", "20",
            "--download",
            "--output-dir", str(PIPE_DIR / "outputs" / f"multi_sku_t2v_videos_{label}"),
            "--videos-dir", str(VIDEOS_DIR),
            "--poll-max-seconds", "1500",
        ])
        if rc != 0:
            log(f"  [WARN] phase_d exit={rc} for round {round_no}")

        # 统计本轮成功视频
        round_dir = PIPE_DIR / "outputs" / f"multi_sku_t2v_videos_{label}"
        round_tasks = round_dir / "multi_sku_t2v_tasks.jsonl"
        ok = fail = 0
        if round_tasks.exists():
            for line in round_tasks.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line:
                    continue
                r2 = json.loads(line)
                if r2.get("status") == "succeeded":
                    ok += 1
                else:
                    fail += 1
        total_videos = sum(1 for _ in VIDEOS_DIR.iterdir() if _.suffix == ".mp4")
        elapsed_min = (time.time() - start_t) / 60
        log(f"  ROUND {round_no} done: ok={ok} fail={fail}, 累计视频 {total_videos}, 耗时 {elapsed_min:.1f}min")

    log("==== 全部 ROUND 完成 ====")
    total_videos = sum(1 for _ in VIDEOS_DIR.iterdir() if _.suffix == ".mp4")
    log(f"最终累计视频: {total_videos}")


if __name__ == "__main__":
    main()
