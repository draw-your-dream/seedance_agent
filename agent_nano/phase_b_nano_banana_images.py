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

DEFAULT_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
DEFAULT_API_BASE = os.environ.get("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")
DEFAULT_OUTPUT_DIR = OUTPUT_DIR / "nano_banana_images"
DEFAULT_REFERENCE_IMAGE = PIPELINE_DIR / "reference.png"
DEFAULT_REFERENCE_INSTRUCTION = (
    "请参考输入图片中的蘑菇TUTU角色外观与整体风格生成画面，保持角色身份一致；"
    "参考图只用于角色外观和风格，不要照搬参考图背景。"
)
DEFAULT_IMAGE_GENERATION_INSTRUCTION = (
    "请根据下面的文生图 prompt 直接生成一张 9:16 竖版首帧图片。"
    "画布必须是竖版，宽高比必须为 9:16，严禁生成横版或方图。"
    "小蘑菇TUTU的画面占比不得超过整体画面大小的五分之一。"
    "场景中不要出现尘埃，不要出现任何发光的粒子状，光线要柔和，不要有光点或圆形光斑。"
    "小蘑菇TUTU位置要符合物理规律，禁止悬浮，物理穿模等。"
    "只需要生成图片，不要只回复文字说明。"
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def gemini_url(args: argparse.Namespace) -> str:
    if args.api_url:
        return args.api_url
    return f"{args.api_base.rstrip('/')}/models/{args.model}:generateContent"


def image_part(path: Path) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"inline_data": {"mime_type": mime_type, "data": data}}


def post_json(url: str, payload: dict[str, Any], api_key: str, timeout: int) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-goog-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini HTTP {exc.code}: {body}") from exc


def build_payload(prompt: str, args: argparse.Namespace) -> dict[str, Any]:
    full_prompt = prompt
    if args.image_generation_instruction:
        full_prompt = f"{args.image_generation_instruction.strip()}\n\n{full_prompt.strip()}"
    if args.reference_image and args.reference_instruction:
        full_prompt = f"{args.reference_instruction.strip()}\n\n{full_prompt.strip()}"
    if args.prompt_suffix:
        full_prompt = f"{full_prompt.strip()}\n\n{args.prompt_suffix.strip()}"

    generation_config: dict[str, Any] = {
        "temperature": args.temperature,
        "topP": args.top_p,
    }
    if args.response_modalities:
        generation_config["responseModalities"] = args.response_modalities
    if args.aspect_ratio:
        generation_config["imageConfig"] = {"aspectRatio": args.aspect_ratio}

    parts: list[dict[str, Any]] = [{"text": full_prompt}]
    if args.reference_image:
        if not args.reference_image.exists():
            raise FileNotFoundError(f"Reference image not found: {args.reference_image}")
        parts.append(image_part(args.reference_image))

    return {
        "contents": [
            {
                "role": "user",
                "parts": parts,
            }
        ],
        "generationConfig": generation_config,
    }


def response_text(response: dict[str, Any]) -> str:
    chunks: list[str] = []
    for candidate in response.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
    return "\n".join(chunks)


def iter_inline_images(response: dict[str, Any]) -> list[tuple[str, bytes]]:
    images: list[tuple[str, bytes]] = []
    for candidate in response.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if not isinstance(inline, dict):
                continue
            data = inline.get("data")
            if not isinstance(data, str):
                continue
            mime_type = str(inline.get("mimeType") or inline.get("mime_type") or "image/png")
            images.append((mime_type, base64.b64decode(data)))
    return images


def suffix_for_mime(mime_type: str) -> str:
    return mimetypes.guess_extension(mime_type) or ".png"


def save_inline_images(response: dict[str, Any], image_dir: Path, index: int) -> list[str]:
    image_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for output_index, (mime_type, data) in enumerate(iter_inline_images(response), start=1):
        suffix = suffix_for_mime(mime_type)
        target = image_dir / f"{index:05d}_{output_index:02d}{suffix}"
        target.write_bytes(data)
        saved.append(str(target.resolve()))
    return saved


def build_result(index: int, row: dict[str, Any], prompt: str, args: argparse.Namespace) -> dict[str, Any]:
    return {
        "index": index,
        "event_id": row.get("event_id"),
        "context_id": row.get("context_id"),
        "title": row.get("title"),
        "input": {
            "prompt": prompt,
            "reference_image": str(args.reference_image) if args.reference_image else None,
            "reference_instruction": args.reference_instruction,
            "image_generation_instruction": args.image_generation_instruction,
        },
    }


def sort_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(results, key=lambda item: int(item.get("index", 0)))


def create_image_for_row(
    index: int,
    row: dict[str, Any],
    args: argparse.Namespace,
    *,
    api_key: str,
    url: str,
    image_dir: Path,
) -> dict[str, Any]:
    prompt = str(row.get(args.prompt_field, "")).strip()
    if not prompt:
        raise ValueError(f"row missing prompt field: {args.prompt_field}")

    payload = build_payload(prompt, args)
    result = build_result(index, row, prompt, args)
    if args.keep_request:
        result["request"] = payload
    if args.dry_run:
        result["status"] = "dry_run"
        return result

    print(f"[create] {index + 1:05d} {row.get('title', '')}")
    last_error: Exception | None = None
    for attempt in range(1, args.retries + 2):
        try:
            response = post_json(url, payload, api_key=api_key, timeout=args.timeout)
            saved_files = save_inline_images(response, image_dir, index + 1)
            status = "succeeded" if saved_files else "failed"
            result.update(
                {
                    "status": status,
                    "model": args.model,
                    "response_text": response_text(response),
                    "downloaded_files": saved_files,
                    "image_path": saved_files[0] if saved_files else None,
                    "output": saved_files,
                }
            )
            if not saved_files:
                result["error"] = "Gemini response did not contain inline image data."
                result["raw_response"] = response
            return result
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"[retry] {index + 1:05d} attempt {attempt} failed: {exc}")
            if attempt <= args.retries:
                time.sleep(args.retry_sleep)

    result.update({"status": "failed", "error": repr(last_error)})
    return result


def run(args: argparse.Namespace) -> Path:
    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key and not args.dry_run:
        raise RuntimeError("Missing GEMINI_API_KEY. Set env GEMINI_API_KEY or pass --api-key.")

    rows = read_jsonl(args.input_jsonl)
    selected = rows[args.start : args.start + args.limit if args.limit else None]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    image_dir = args.output_dir / "images"
    results_path = args.output_dir / "nano_banana_predictions.jsonl"

    results: list[dict[str, Any]] = read_jsonl(results_path) if results_path.exists() else []
    finished_indices = {
        int(row["index"])
        for row in results
        if row.get("status") in {"succeeded", "failed", "dry_run"} and "index" in row
    }
    url = gemini_url(args)

    tasks: list[tuple[int, dict[str, Any]]] = []
    task_indices: set[int] = set()
    for offset, row in enumerate(selected):
        index = args.start + offset
        if index in finished_indices and not args.force:
            continue
        tasks.append((index, row))
        task_indices.add(index)

    if args.force and task_indices:
        results = [
            row for row in results
            if int(row.get("index", -1)) not in task_indices
        ]

    if tasks:
        print(f"[phase-b] generating {len(tasks)} image(s), concurrency={max(1, args.concurrency)}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as executor:
        futures = [
            executor.submit(
                create_image_for_row,
                index,
                row,
                args,
                api_key=api_key or "",
                url=url,
                image_dir=image_dir,
            )
            for index, row in tasks
        ]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            write_jsonl(results_path, sort_results(results))

    write_jsonl(results_path, sort_results(results))
    return results_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase B: use Gemini Nano Banana to turn Phase A prompts into images.")
    parser.add_argument(
        "--input-jsonl",
        type=Path,
        default=PIPELINE_DIR / "outputs" / "gemini_25_flash_50_b25" / "phase_a_text_to_image_prompts.jsonl",
        help="Path to phase_a_text_to_image_prompts.jsonl",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--prompt-field", default="text_to_image_prompt")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--api-url", help="Override full Gemini generateContent URL.")
    parser.add_argument("--api-key")
    parser.add_argument("--reference-image", type=Path, default=DEFAULT_REFERENCE_IMAGE)
    parser.add_argument("--no-reference-image", action="store_true", help="Do not send reference.png to Nano Banana.")
    parser.add_argument("--reference-instruction", default=DEFAULT_REFERENCE_INSTRUCTION)
    parser.add_argument("--image-generation-instruction", default=DEFAULT_IMAGE_GENERATION_INSTRUCTION)
    parser.add_argument("--prompt-suffix", default="")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--response-modalities", nargs="+", default=["IMAGE"])
    parser.add_argument("--aspect-ratio", default="9:16")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-sleep", type=float, default=5.0)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-request", action="store_true", help="Store full request JSONL, including base64 reference image.")
    args = parser.parse_args()
    if args.no_reference_image:
        args.reference_image = None
    return args


def main() -> None:
    path = run(parse_args())
    print(json.dumps({"predictions_jsonl": str(path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
