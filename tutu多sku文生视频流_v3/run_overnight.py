"""通宵批跑：800 条 = 8 轮 × 100 条全流程。每轮：Phase A → B → C → D 流式归档。

每轮结束后：
  1) 把本批 timeout/running 任务追加到 pending_rescue.jsonl
  2) 立即跑一次 rescue（重新查询 Ark，能下就下、归档）
  3) 等 30 秒进入下一轮

全部 8 轮跑完后，进入"持续轮询"阶段：每 5 分钟重新跑 rescue，最多 6 次（30 分钟），
直到 pending_rescue.jsonl 清空。

最终全部归档到 outputs/2500多sku/，编号自动续接。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

PIPE_DIR = Path(r"F:\workspace\tutu内容\tutu多sku文生视频流")
PYTHON = Path(r"F:\workspace\tutu内容\_tools\python311\python.exe")
LOG = PIPE_DIR / "run_overnight.log"

BATCHES = 5
PER_BATCH = 100
START_SEED = 20260701  # 错开种子避开历史 night0X
LABEL_PREFIX = "ext"  # ext01 - ext05

env = os.environ.copy()
env["GEMINI_API_KEY"] = "AIzaSyAufSuYD4VKs_ki1351JdyT816gZYlUqN4"
env["ARK_API_KEY"] = "ea0b480a-411d-4f9f-bb25-b3cca83d0a27"
env.pop("GEMINI_URL", None)


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    # Windows GBK 控制台不支持 emoji；编码失败时 fallback 到 ascii
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_step(args: list[str], must_succeed: bool) -> bool:
    log(f"  $ {' '.join(args)}")
    proc = subprocess.run(args, cwd=PIPE_DIR, env=env)
    if proc.returncode != 0:
        log(f"  [WARN] exit={proc.returncode}")
        return False
    return True


def filter_phase_c_to_label(label: str) -> Path:
    src = PIPE_DIR / "outputs" / "multi_sku_t2v_prompts" / "phase_c_multi_sku_t2v_prompts.jsonl"
    dst = PIPE_DIR / "outputs" / "multi_sku_t2v_prompts" / f"phase_c_{label}.jsonl"
    cnt = 0
    with src.open(encoding="utf-8-sig") as f, dst.open("w", encoding="utf-8") as g:
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


# ============================================================
# 自动轮询补救：失败/timeout 的任务持续重新查询 Ark + 下载归档
# ============================================================

ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"
PKG_DIR = PIPE_DIR / "outputs" / "2500多sku"
VIDEO_DIR = PIPE_DIR / "outputs" / "multi_sku_t2v_videos" / "videos"
TASKS_JSONL = PIPE_DIR / "outputs" / "multi_sku_t2v_videos" / "multi_sku_t2v_tasks.jsonl"
PENDING_FILE = PIPE_DIR / "outputs" / "multi_sku_t2v_videos" / "pending_rescue.jsonl"


def safe_filename(value: str, maxlen: int = 40) -> str:
    value = "".join("_" if c in '\\/:*?"<>|' else c for c in value).strip()
    return (value or "video")[:maxlen]


def append_pending_from_current_batch() -> int:
    """从当前 tasks.jsonl 中找出 timeout / running / submit_failed 的任务追加到 pending_rescue.jsonl。"""
    if not TASKS_JSONL.exists():
        return 0
    new_pending = []
    for line in TASKS_JSONL.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("status") in {"timeout", "running", "submit_failed", "queued", "created"} and r.get("task_id"):
            new_pending.append(r)
    if not new_pending:
        return 0
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PENDING_FILE.open("a", encoding="utf-8") as f:
        for r in new_pending:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(new_pending)


def load_cid_to_nnnn() -> dict[str, str]:
    """扫归档目录的 NNNN.txt，建立 context_id → NNNN 映射。"""
    out: dict[str, str] = {}
    if not PKG_DIR.exists():
        return out
    for txt in PKG_DIR.glob("*.txt"):
        try:
            content = txt.read_text(encoding="utf-8")
            m = re.search(r"context_id:\s*(\S+)", content)
            if m:
                out[m.group(1)] = txt.stem
        except Exception:
            continue
    return out


def query_ark_task(task_id: str, api_key: str, timeout: int = 60) -> dict | None:
    req = urllib.request.Request(
        f"{ARK_BASE}/{task_id}", headers={"Authorization": f"Bearer {api_key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def download_one_url(url: str, out_path: Path, timeout: int = 180) -> bool:
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            out_path.write_bytes(resp.read())
        return True
    except Exception:
        return False


def rescue_one(task: dict, cid_to_nnnn: dict[str, str], api_key: str) -> tuple[str, str]:
    """对一条 pending 任务做一次救援查询。返回 (status_code, msg)。

    status_code: 'rescued' / 'still-pending' / 'failed' / 'no-mapping' / 'query-fail'
    """
    tid = task.get("task_id")
    cid = task.get("context_id")
    if not tid or not cid:
        return ("failed", "missing task_id or context_id")

    resp = query_ark_task(tid, api_key)
    if resp is None:
        return ("query-fail", "ark query exception")

    status = (resp.get("status") or resp.get("data", {}).get("status") or "").lower()
    content = resp.get("content") or resp.get("data", {}).get("content") or {}
    video_url = content.get("video_url") if isinstance(content, dict) else None

    if status in {"failed", "cancelled", "canceled"}:
        return ("failed", f"ark says {status}")
    if status not in {"succeeded", "success"} or not video_url:
        return ("still-pending", f"ark says {status}, video_url={'yes' if video_url else 'no'}")

    # 已成功，下载 + 归档
    title = safe_filename(str(task.get("title") or task.get("index") or "video"))
    video_path = VIDEO_DIR / f"{int(task.get('index', 0)):05d}_sku{task.get('sku_index')}_{title}.mp4"
    if not download_one_url(str(video_url), video_path):
        return ("query-fail", "video download exception")

    nnnn = cid_to_nnnn.get(cid)
    if not nnnn:
        return ("no-mapping", f"no NNNN for {cid}, video saved at {video_path.name}")

    # 拷贝到归档目录
    try:
        shutil.copy2(video_path, PKG_DIR / f"{nnnn}.mp4")
    except Exception as exc:  # noqa: BLE001
        return ("query-fail", f"copy mp4 exception: {exc}")

    # 重写 NNNN.txt：把 task_id / status / video_url 填进 Phase D 段
    txt_path = PKG_DIR / f"{nnnn}.txt"
    if txt_path.exists():
        try:
            content_txt = txt_path.read_text(encoding="utf-8")
            content_txt = re.sub(r"(- task_id:).*", lambda m: f"{m.group(1)} {tid}", content_txt)
            content_txt = re.sub(r"(- status:).*", lambda m: f"{m.group(1)} {status}", content_txt)
            content_txt = re.sub(r"(- video_url:).*", lambda m: f"{m.group(1)} {video_url}", content_txt)
            txt_path.write_text(content_txt, encoding="utf-8")
        except Exception:
            pass

    return ("rescued", f"NNNN={nnnn}")


def run_rescue_pass(api_key: str) -> tuple[int, int, int]:
    """跑一轮救援。返回 (rescued, still_pending, failed) 计数。剩余 pending 写回文件。"""
    if not PENDING_FILE.exists():
        return (0, 0, 0)

    pending: list[dict] = []
    for line in PENDING_FILE.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            pending.append(json.loads(line))
    if not pending:
        return (0, 0, 0)

    cid_to_nnnn = load_cid_to_nnnn()
    rescued, still_pending, failed = 0, 0, 0
    remaining: list[dict] = []

    log(f"  [rescue] {len(pending)} pending tasks to check...")
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(rescue_one, t, cid_to_nnnn, api_key): t for t in pending}
        for fut in as_completed(futures):
            t = futures[fut]
            code, msg = fut.result()
            if code == "rescued":
                rescued += 1
            elif code == "still-pending":
                still_pending += 1
                remaining.append(t)
            elif code == "query-fail":
                # 网络问题之类，下次再试
                still_pending += 1
                remaining.append(t)
            else:  # failed / no-mapping
                failed += 1
                if code == "no-mapping":
                    # 没有 NNNN 映射就保留，下次说不定有了
                    remaining.append(t)

    # 写回剩余
    with PENDING_FILE.open("w", encoding="utf-8") as f:
        for r in remaining:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    log(f"  [rescue] rescued={rescued}  still-pending={still_pending}  failed={failed}")
    return (rescued, still_pending, failed)


def main() -> None:
    log(f"=== overnight run started: {BATCHES} batches × {PER_BATCH} prompts ===")
    LOG.write_text("", encoding="utf-8") if not LOG.exists() else None

    for i in range(1, BATCHES + 1):
        label = f"{LABEL_PREFIX}{i:02d}"
        seed = START_SEED + i
        log(f"\n========== Batch {i}/{BATCHES} — label={label} seed={seed} ==========")

        # Phase A
        if not run_step(
            [str(PYTHON), "phase_a.py", "--count", str(PER_BATCH), "--run-label", label, "--seed", str(seed)],
            must_succeed=True,
        ):
            log(f"  Batch {i} Phase A failed, skip rest of this batch")
            continue

        # Phase B
        if not run_step(
            [str(PYTHON), "phase_b_multi_sku_blueprints.py", "--seed", str(seed)],
            must_succeed=True,
        ):
            log(f"  Batch {i} Phase B failed, skip rest of this batch")
            continue

        # Phase C
        if not run_step(
            [str(PYTHON), "phase_c_multi_sku_t2v_prompts.py", "--limit", str(PER_BATCH), "--force"],
            must_succeed=True,
        ):
            log(f"  Batch {i} Phase C failed, skip rest of this batch")
            continue

        # Filter to this batch
        try:
            filtered = filter_phase_c_to_label(label)
        except Exception as exc:  # noqa: BLE001
            log(f"  filter failed: {exc}")
            continue

        # Phase D parallel + 流式归档（不强制成功——timeout 是部分失败可接受）
        run_step(
            [
                str(PYTHON), "phase_d_parallel.py",
                "--prompts-jsonl", str(filtered),
                "--limit", str(PER_BATCH),
                "--workers", "10",
                "--download", "--force",
                "--package-dir", str(PIPE_DIR / "outputs" / "2500多sku"),
            ],
            must_succeed=False,
        )

        # 把本批没成功的（timeout/running 等）追加到 pending_rescue，避免被下一批 tasks.jsonl 覆盖丢失
        added = append_pending_from_current_batch()
        log(f"  added {added} pending tasks to rescue queue")

        # 立即跑一次 rescue（也许 Ark 已经渲染好了）
        run_rescue_pass(env["ARK_API_KEY"])

        # 简单进度统计
        mp4_count = sum(1 for p in PKG_DIR.glob("*.mp4"))
        log(f"  Batch {i} done; total mp4 in 2500多sku/: {mp4_count}")

        if i < BATCHES:
            log(f"  sleeping 30s before next batch")
            time.sleep(30)

    # ====== 全部 8 轮结束后，进入"持续轮询"补救模式 ======
    log(f"\n=== batches done, entering rescue polling phase ===")
    max_passes = 8
    wait_seconds = 300  # 5 分钟一次
    for p in range(1, max_passes + 1):
        log(f"\n[rescue-pass {p}/{max_passes}] sleep {wait_seconds}s then re-poll Ark")
        time.sleep(wait_seconds)
        rescued, still_pending, failed = run_rescue_pass(env["ARK_API_KEY"])
        if still_pending == 0:
            log(f"  no more pending tasks, rescue complete")
            break

    log(f"\n=== ALL DONE ===")
    mp4_count = sum(1 for p in PKG_DIR.glob("*.mp4"))
    txt_count = sum(1 for p in PKG_DIR.glob("*.txt"))
    log(f"  final 2500多sku/: {mp4_count} mp4, {txt_count} txt")
    if PENDING_FILE.exists():
        leftover = sum(1 for line in PENDING_FILE.read_text(encoding="utf-8-sig").splitlines() if line.strip())
        log(f"  pending_rescue.jsonl 还剩 {leftover} 条任务（醒来后可手动再跑 rescue）")


if __name__ == "__main__":
    main()
