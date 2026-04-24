"""Phase D 并行版：同时把 N 条 prompt 都提交到 Ark，再并行轮询，最后下载。

比原 `phase_d_multi_sku_t2v_videos.py` 的串行 wait 快很多。
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


PIPELINE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PIPELINE_DIR / "outputs"

SKU_DIR = PIPELINE_DIR / "sku"
HAND_FOOT_IMAGE = SKU_DIR / "hand_foot.jpg"
MOUTH_IMAGE = SKU_DIR / "mouth.jpg"

DEFAULT_PROMPTS = OUTPUT_DIR / "multi_sku_t2v_prompts" / "phase_c_multi_sku_t2v_prompts.jsonl"
DEFAULT_OUTPUT_DIR = OUTPUT_DIR / "multi_sku_t2v_videos"

ARK_API_URL = "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"
ARK_MODEL = "doubao-seedance-2-0-260128"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + ("\n" if records else ""),
        encoding="utf-8",
    )


def local_image_to_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def build_payload(
    prompt: str,
    character_url: str,
    hand_foot_url: str,
    mouth_url: str,
    *,
    model: str,
    ratio: str,
    duration: int,
    generate_audio: bool,
    watermark: bool,
) -> dict[str, Any]:
    return {
        "model": model,
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": character_url}, "role": "reference_image"},
            {"type": "image_url", "image_url": {"url": hand_foot_url}, "role": "reference_image"},
            {"type": "image_url", "image_url": {"url": mouth_url}, "role": "reference_image"},
        ],
        "generate_audio": generate_audio,
        "ratio": ratio,
        "duration": duration,
        "watermark": watermark,
    }


def post_json(url: str, payload: dict[str, Any], api_key: str, timeout: int) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc


def get_json(url: str, api_key: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc


def download_file(url: str, path: Path, timeout: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        path.write_bytes(response.read())


def safe_filename(value: str) -> str:
    value = "".join("_" if char in '\\/:*?"<>|' else char for char in value).strip()
    return value or "video"


def find_task_id(response: dict[str, Any]) -> str | None:
    for key in ("id", "task_id"):
        if response.get(key):
            return str(response[key])
    data = response.get("data")
    if isinstance(data, dict):
        for key in ("id", "task_id"):
            if data.get(key):
                return str(data[key])
    return None


def find_video_url(response: dict[str, Any]) -> str | None:
    content = response.get("content")
    if isinstance(content, dict) and content.get("video_url"):
        return str(content["video_url"])
    data = response.get("data")
    if isinstance(data, dict):
        content = data.get("content")
        if isinstance(content, dict) and content.get("video_url"):
            return str(content["video_url"])
    return None


def submit_one(
    record: dict[str, Any],
    hand_foot_url: str,
    mouth_url: str,
    args: argparse.Namespace,
    api_key: str,
) -> dict[str, Any]:
    index = int(record["index"])
    prompt = str(record.get("seedance_t2v_prompt") or "").strip()
    if not prompt:
        raise ValueError(f"Missing prompt for index {index}")
    sku_path = Path(record.get("sku_image_path") or "")
    if not sku_path.exists():
        raise FileNotFoundError(f"SKU missing: {sku_path}")
    character_url = local_image_to_data_url(sku_path)

    payload = build_payload(
        prompt=prompt,
        character_url=character_url,
        hand_foot_url=hand_foot_url,
        mouth_url=mouth_url,
        model=args.model,
        ratio=args.ratio,
        duration=args.duration,
        generate_audio=args.generate_audio,
        watermark=args.watermark,
    )
    payload_path = args.payloads_dir / f"{index:05d}.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[submit] {index:02d} sku={record.get('sku_index')} {record.get('title', '')}", flush=True)
    response = post_json(args.api_url, payload, api_key=api_key, timeout=args.timeout)
    task_id = find_task_id(response)
    print(f"[submitted] {index:02d} task_id={task_id}", flush=True)
    return {
        "index": index,
        "context_id": record.get("context_id"),
        "title": record.get("title"),
        "sku_index": record.get("sku_index"),
        "prompt": prompt,
        "payload_path": str(payload_path),
        "task_id": task_id,
        "status": "created",
        "ratio": args.ratio,
        "duration": args.duration,
    }


def poll_one(
    task: dict[str, Any],
    args: argparse.Namespace,
    api_key: str,
) -> dict[str, Any]:
    task_id = task["task_id"]
    url = f"{args.api_url}/{task_id}"
    deadline = time.time() + args.poll_max_seconds
    while time.time() < deadline:
        try:
            resp = get_json(url, api_key=api_key, timeout=args.timeout)
        except Exception as exc:  # noqa: BLE001
            print(f"[poll-err] {task['index']:02d} {exc}", flush=True)
            time.sleep(args.poll_interval)
            continue
        status = str(resp.get("status") or resp.get("data", {}).get("status") or "").lower()
        video_url = find_video_url(resp)
        if video_url:
            task["video_url"] = video_url
        task["status"] = status or "polled"
        task["last_poll_response"] = resp
        if status in {"succeeded", "success", "failed", "cancelled", "canceled"}:
            print(f"[poll-done] {task['index']:02d} status={status}", flush=True)
            return task
        print(f"[poll] {task['index']:02d} status={status}", flush=True)
        time.sleep(args.poll_interval)
    task["status"] = "timeout"
    return task


def download_one(task: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if not task.get("video_url"):
        return task
    title = safe_filename(str(task.get("title") or task["index"]))[:40]
    path = args.videos_dir / f"{task['index']:05d}_sku{task.get('sku_index')}_{title}.mp4"
    if args.force or not path.exists():
        try:
            download_file(str(task["video_url"]), path, timeout=args.timeout)
            task["download_path"] = str(path.resolve())
            print(f"[download] {task['index']:02d} -> {path.name}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[download-err] {task['index']:02d} {exc}", flush=True)
    else:
        task["download_path"] = str(path.resolve())
    return task


def run(args: argparse.Namespace) -> list[dict[str, Any]]:
    api_key = args.api_key or os.environ.get("ARK_API_KEY")
    if not api_key:
        raise ValueError("Missing ARK_API_KEY. Set env ARK_API_KEY or pass --api-key.")

    prompts = read_jsonl(args.prompts_jsonl)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.payloads_dir = args.output_dir / "payloads"
    args.videos_dir = args.videos_dir or (args.output_dir / "videos")
    args.payloads_dir.mkdir(parents=True, exist_ok=True)
    args.videos_dir.mkdir(parents=True, exist_ok=True)
    output_jsonl = args.output_dir / "multi_sku_t2v_tasks.jsonl"

    hand_foot_url = local_image_to_data_url(HAND_FOOT_IMAGE)
    mouth_url = local_image_to_data_url(MOUTH_IMAGE)

    selected = prompts[: args.limit] if args.limit else prompts
    print(f"[batch] submitting {len(selected)} tasks in parallel (workers={args.workers})", flush=True)

    # Step 1: submit all tasks in parallel (容错：单条失败不影响其他)
    submitted: list[dict[str, Any]] = []

    def _try_submit(record: dict[str, Any]) -> dict[str, Any]:
        try:
            return submit_one(record, hand_foot_url, mouth_url, args, api_key)
        except Exception as exc:  # noqa: BLE001
            index = int(record["index"])
            msg = str(exc)[:400]
            print(f"[submit-fail] {index:02d} {msg}", flush=True)
            return {
                "index": index,
                "context_id": record.get("context_id"),
                "title": record.get("title"),
                "sku_index": record.get("sku_index"),
                "prompt": str(record.get("seedance_t2v_prompt") or "").strip(),
                "status": "submit_failed",
                "error": msg,
                "ratio": args.ratio,
                "duration": args.duration,
            }

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_try_submit, r) for r in selected]
        for f in concurrent.futures.as_completed(futures):
            submitted.append(f.result())
    submitted.sort(key=lambda t: t["index"])
    write_jsonl(output_jsonl, submitted)
    pollable = [t for t in submitted if t.get("task_id")]
    print(f"[batch] submitted ok={len(pollable)}/{len(submitted)}, polling {len(pollable)} (interval={args.poll_interval}s)", flush=True)

    # Step 2: poll tasks that have task_id
    finished_ids: set[int] = set()
    finished: list[dict[str, Any]] = []
    if pollable:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(poll_one, t, args, api_key) for t in pollable]
            for f in concurrent.futures.as_completed(futures):
                done = f.result()
                finished.append(done)
                finished_ids.add(done["index"])
                snapshot = sorted(
                    [t for t in submitted if t["index"] not in finished_ids] + finished,
                    key=lambda t: t["index"],
                )
                write_jsonl(output_jsonl, snapshot)
    # merge failed submits
    merged: list[dict[str, Any]] = finished + [t for t in submitted if t["index"] not in finished_ids]
    merged.sort(key=lambda t: t["index"])

    # Step 3: download in parallel
    if args.download:
        print(f"[batch] downloading", flush=True)
        dl_targets = [t for t in merged if t.get("video_url")]
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(download_one, t, args) for t in dl_targets]
            for f in concurrent.futures.as_completed(futures):
                f.result()
    finished = merged

    write_jsonl(output_jsonl, finished)
    return finished


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase D 并行版：一次性提交 N 条到 Ark Seedance，并行轮询，然后下载",
    )
    parser.add_argument("--prompts-jsonl", type=Path, default=DEFAULT_PROMPTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--videos-dir", type=Path, help="下载目录；默认 output-dir/videos")
    parser.add_argument("--api-url", default=ARK_API_URL)
    parser.add_argument("--api-key")
    parser.add_argument("--model", default=ARK_MODEL)
    parser.add_argument("--ratio", default="9:16")
    parser.add_argument("--duration", type=int, default=5)
    parser.add_argument("--generate-audio", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--watermark", action="store_true")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--workers", type=int, default=5, help="并行线程数；建议 <= limit")
    parser.add_argument("--timeout", type=int, default=180, help="单次 HTTP 超时秒数")
    parser.add_argument("--poll-interval", type=float, default=15.0)
    parser.add_argument("--poll-max-seconds", type=int, default=900, help="单个任务轮询总超时，默认 15 分钟")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tasks = run(args)
    succ = sum(1 for t in tasks if t.get("status") in {"succeeded", "success"})
    print(json.dumps({"total": len(tasks), "succeeded": succ, "output": str(args.output_dir / "multi_sku_t2v_tasks.jsonl")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
