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

DEFAULT_PROMPTS = OUTPUT_DIR / "seedance_i2v_prompts_gemini_31_pro_preview_50" / "phase_c_seedance_i2v_prompts.jsonl"
DEFAULT_IMAGE_PREDICTIONS = OUTPUT_DIR / "nano_banana_images" / "nano_banana_predictions.jsonl"
DEFAULT_REFERENCE_IMAGE = PIPELINE_DIR / "reference.png"
DEFAULT_OUTPUT_DIR = OUTPUT_DIR / "seedance_videos"

ARK_API_URL = "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"
ARK_MODEL = "doubao-seedance-2-0-260128"
TERMINAL_STATUSES = {"succeeded", "success", "failed", "cancelled", "canceled"}


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
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + ("\n" if records else ""),
        encoding="utf-8",
    )


def is_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://") or value.startswith("data:")


def local_image_to_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def image_url(value: str | None, *, allow_data_url: bool) -> str:
    if not value:
        raise ValueError("Missing image URL/path")
    if is_url(value):
        return value
    path = Path(value)
    if not path.exists():
        raise FileNotFoundError(f"Image path does not exist and is not a URL: {value}")
    if not allow_data_url:
        raise ValueError(
            f"Ark 的 image_url 通常需要公网 URL。当前是本地文件：{value}。"
            "请传 --reference-image-url，或加 --allow-data-url-for-local-images 试用 data URL。"
        )
    return local_image_to_data_url(path)


def load_first_frame_urls(path: Path) -> dict[int, str]:
    urls: dict[int, str] = {}
    for record in read_jsonl(path):
        output = record.get("output")
        if isinstance(output, list) and output:
            urls[int(record["index"]) + 1] = str(output[0])
        elif isinstance(output, str):
            urls[int(record["index"]) + 1] = output
        elif record.get("image_path"):
            urls[int(record["index"]) + 1] = str(record["image_path"])
        elif isinstance(record.get("downloaded_files"), list) and record["downloaded_files"]:
            urls[int(record["index"]) + 1] = str(record["downloaded_files"][0])
    return urls


def build_payload(
    prompt: str,
    first_frame_url: str,
    reference_image_url: str,
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
            {
                "type": "text",
                "text": prompt,
            },
            {
                "type": "image_url",
                "image_url": {"url": first_frame_url},
                "role": "reference_image",
            },
            {
                "type": "image_url",
                "image_url": {"url": reference_image_url},
                "role": "reference_image",
            },
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
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc


def get_json(url: str, api_key: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
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


def status_is_terminal(status: Any) -> bool:
    return str(status or "").lower() in TERMINAL_STATUSES


def record_is_done(record: dict[str, Any], *, require_download: bool) -> bool:
    if str(record.get("status") or "").lower() not in {"succeeded", "success"}:
        return False
    if not require_download:
        return True
    download_path = record.get("download_path")
    return bool(download_path and Path(str(download_path)).exists())


def save_records(path: Path, records: dict[int, dict[str, Any]]) -> None:
    write_jsonl(path, [records[key] for key in sorted(records)])


def run(args: argparse.Namespace) -> list[dict[str, Any]]:
    api_key = args.api_key or os.environ.get("ARK_API_KEY")
    if args.execute and not api_key:
        raise ValueError("Missing ARK_API_KEY. Set env ARK_API_KEY or pass --api-key.")

    prompts = read_jsonl(args.prompts_jsonl)
    first_frame_urls = load_first_frame_urls(args.image_predictions_jsonl)
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_jsonl = output_dir / "seedance_tasks.jsonl"
    payloads_dir = output_dir / "payloads"
    videos_dir = args.videos_dir or (output_dir / "videos")
    payloads_dir.mkdir(parents=True, exist_ok=True)
    videos_dir.mkdir(parents=True, exist_ok=True)

    existing = read_jsonl(output_jsonl)
    done = {int(record["index"]): record for record in existing if "index" in record}
    records = dict(done)

    reference_value = args.reference_image_url or str(args.reference_image)
    reference_url = image_url(reference_value, allow_data_url=args.allow_data_url_for_local_images)

    selected = prompts[: args.limit] if args.limit else prompts
    pending_indices: set[int] = set()
    for record in selected:
        index = int(record["index"])
        if index in records and not args.force and record_is_done(records[index], require_download=args.download):
            print(f"[skip] {index:02d} {record.get('context_id', '')}")
            continue

        first_frame_value = first_frame_urls.get(index) or record.get("image_url") or record.get("image_path")
        first_frame_url = image_url(str(first_frame_value), allow_data_url=args.allow_data_url_for_local_images)
        payload = build_payload(
            prompt=record["seedance_i2v_prompt"],
            first_frame_url=first_frame_url,
            reference_image_url=reference_url,
            model=args.model,
            ratio=args.ratio,
            duration=args.duration,
            generate_audio=args.generate_audio,
            watermark=args.watermark,
        )
        payload_path = payloads_dir / f"{index:05d}.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        task_record = {
            "index": index,
            "context_id": record.get("context_id"),
            "event_id": record.get("event_id"),
            "title": record.get("title"),
            "prompt": record.get("seedance_i2v_prompt"),
            "first_frame_url": first_frame_url,
            "reference_image_source": args.reference_image_url or str(args.reference_image),
            "reference_image_url": reference_url if not reference_url.startswith("data:") else "[data-url-from-local-reference-image]",
            "payload_path": str(payload_path),
            "status": "dry_run",
        }
        if not args.execute:
            print(f"[dry-run] {index:02d} {record.get('title', '')}")

        records[index] = task_record
        pending_indices.add(index)
        save_records(output_jsonl, records)

    if args.execute:
        to_create = [records[index] for index in sorted(pending_indices) if not records[index].get("task_id")]
        if to_create:
            print(f"[create] submitting {len(to_create)} task(s), concurrency={max(1, args.concurrency)}")

        def create_task(task_record: dict[str, Any]) -> tuple[int, dict[str, Any] | None, str | None]:
            payload_data = json.loads(Path(str(task_record["payload_path"])).read_text(encoding="utf-8"))
            try:
                response = post_json(args.api_url, payload_data, api_key=api_key or "", timeout=args.timeout)
                return int(task_record["index"]), response, None
            except Exception as exc:  # noqa: BLE001
                return int(task_record["index"]), None, repr(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as executor:
            future_to_index = {executor.submit(create_task, record): int(record["index"]) for record in to_create}
            for future in concurrent.futures.as_completed(future_to_index):
                index = future_to_index[future]
                _, response, error = future.result()
                task_record = records[index]
                if error:
                    task_record["status"] = "create_error"
                    task_record["error"] = error
                    print(f"[create-error] {index:02d} {error}")
                else:
                    task_record["create_response"] = response
                    task_record["task_id"] = find_task_id(response or {})
                    task_record["status"] = "created"
                    print(f"[created] {index:02d} {task_record.get('task_id')}")
                save_records(output_jsonl, records)

        if args.wait:
            pending = [
                records[index] for index in sorted(pending_indices)
                if records[index].get("task_id") and not record_is_done(records[index], require_download=args.download)
            ]
            while pending:
                next_pending: list[dict[str, Any]] = []
                for task_record in pending:
                    index = int(task_record["index"])
                    try:
                        poll_response = get_json(f"{args.api_url}/{task_record['task_id']}", api_key=api_key or "", timeout=args.timeout)
                        task_record["poll_response"] = poll_response
                        status = str(poll_response.get("status") or poll_response.get("data", {}).get("status") or "")
                        task_record["status"] = status or "polled"
                        video_url = find_video_url(poll_response)
                        if video_url:
                            task_record["video_url"] = video_url
                        if status_is_terminal(task_record.get("status")):
                            print(f"[status] {index:02d} {task_record['status']}")
                        else:
                            next_pending.append(task_record)
                    except Exception as exc:  # noqa: BLE001
                        task_record["status"] = "poll_error"
                        task_record["error"] = repr(exc)
                        print(f"[poll-error] {index:02d} {exc!r}")
                        next_pending.append(task_record)

                    if args.download and task_record.get("video_url") and not task_record.get("download_path"):
                        download_path = videos_dir / f"{index:05d}_{safe_filename(str(task_record.get('title') or index))}.mp4"
                        try:
                            if args.force or not download_path.exists():
                                download_file(str(task_record["video_url"]), download_path, timeout=args.timeout)
                            task_record["download_path"] = str(download_path.resolve())
                            print(f"[downloaded] {index:02d}")
                        except Exception as exc:  # noqa: BLE001
                            task_record["download_error"] = repr(exc)
                            print(f"[download-error] {index:02d} {exc!r}")
                            next_pending.append(task_record)

                save_records(output_jsonl, records)
                pending = [
                    record for record in next_pending
                    if record.get("task_id") and not record_is_done(record, require_download=args.download)
                ]
                if pending:
                    time.sleep(args.poll_interval)

    return [records[key] for key in sorted(records)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase D：提交 Seedance 图生视频任务")
    parser.add_argument("--prompts-jsonl", type=Path, default=DEFAULT_PROMPTS)
    parser.add_argument("--image-predictions-jsonl", type=Path, default=DEFAULT_IMAGE_PREDICTIONS)
    parser.add_argument("--replicate-predictions-jsonl", type=Path, dest="image_predictions_jsonl", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    parser.add_argument("--reference-image", type=Path, default=DEFAULT_REFERENCE_IMAGE)
    parser.add_argument("--reference-image-url", help="reference.png 的公网 URL；不传则使用本地 reference.png 转 data URL")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--videos-dir", type=Path, help="下载视频目录；默认是 output-dir/videos")
    parser.add_argument("--api-url", default=ARK_API_URL)
    parser.add_argument("--api-key")
    parser.add_argument("--model", default=ARK_MODEL)
    parser.add_argument("--ratio", default="9:16")
    parser.add_argument("--duration", type=int, default=15)
    parser.add_argument("--generate-audio", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--watermark", action="store_true")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--download", action="store_true", help="任务完成后下载 video_url 到本地")
    parser.add_argument("--poll-interval", type=float, default=10.0)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--execute", action="store_true", help="实际提交 Ark 任务；不加则只生成 payload dry-run")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--allow-data-url-for-local-images",
        action="store_true",
        default=True,
        help="允许把本地图片转成 data URL 放入 image_url；如果 Ark 不支持，请改用 --reference-image-url。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = run(args)
    print(json.dumps({"count": len(records), "output": str(args.output_dir / "seedance_tasks.jsonl")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
