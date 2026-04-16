from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any


PIPELINE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PIPELINE_DIR / "outputs"

GEMINI_API_KEY = os.environ.get("PICAA_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
GEMINI_URL = os.environ.get("GEMINI_URL", "https://ai.ssnai.com/gemini/v1beta/models/gemini-3.1-pro-preview:generateContent")

DEFAULT_CONTEXTS = OUTPUT_DIR / "gemini_25_flash_50_b25" / "phase_a_contexts.jsonl"
DEFAULT_EVENTS = OUTPUT_DIR / "gemini_25_flash_50_b25" / "phase_a_text_to_image_prompts.jsonl"
DEFAULT_IMAGES_DIR = OUTPUT_DIR / "nano_banana_images" / "images"
DEFAULT_OUTPUT_DIR = OUTPUT_DIR / "seedance_i2v_prompts_gemini_31_pro_preview_50"
SYSTEM_PROMPT_PATH = PIPELINE_DIR / "phase_c_seedance_i2v_system_prompt.md"
OLD_PREFIX = "以输入图片为第一帧，保持整体构图、透视关系与空间结构一致"
NEW_PREFIX = "以输入图片为第一帧，第二张图片为参考图，保持整体构图、透视关系与空间结构一致"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
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


def load_done(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    done: dict[str, dict[str, Any]] = {}
    for record in read_jsonl(path):
        context_id = str(record.get("context_id", ""))
        prompt = str(record.get("seedance_i2v_prompt", "")).strip()
        if context_id and prompt and record.get("status") == "succeeded":
            done[context_id] = record
    return done


def image_part(path: Path) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"inline_data": {"mime_type": mime_type, "data": data}}


def strip_code_fence(text: str) -> str:
    text = text.strip()
    fenced = re.search(r"```(?:text|markdown|json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    return text


def sanitize_seedance_prompt(text: str) -> str:
    replacements = {
        "短小的前肢": "前肢",
        "短小前肢": "前肢",
        "短小的手脚": "手脚",
        "小短手": "手",
        "小短腿": "腿",
        "小手": "手",
        "小脚": "脚",
        "小小的": "",
        "小小": "",
        "一小步": "一步",
        "两小步": "两步",
        "小步子": "步子",
        "小步": "步",
        "小碎步": "细碎步伐",
        "微小的": "轻微的",
        "微小": "轻微",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    if text.startswith(OLD_PREFIX):
        text = NEW_PREFIX + text[len(OLD_PREFIX):]
    return text


def call_gemini_with_image(
    system_prompt: str,
    user_prompt: str,
    first_frame_path: Path,
    timeout: int,
) -> str:
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": f"{system_prompt}\n\n---\n\n{user_prompt}"},
                    image_part(first_frame_path),
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.8,
            "topP": 0.95,
        },
    }
    tmp_dir = OUTPUT_DIR / "_tmp_phase_c"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    payload_path = tmp_dir / "gemini_i2v_request.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = subprocess.run(
        [
            "curl.exe",
            "-s",
            GEMINI_URL,
            "-H",
            "Content-Type: application/json",
            "-H",
            f"X-goog-api-key: {GEMINI_API_KEY}",
            "-X",
            "POST",
            "-d",
            f"@{payload_path}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl failed with code {result.returncode}: {result.stderr.strip()}")
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini returned non-JSON response: {result.stdout[:500]}") from exc
    if "error" in response:
        raise RuntimeError(json.dumps(response["error"], ensure_ascii=False))
    try:
        return response["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(json.dumps(response, ensure_ascii=False)[:1000]) from exc


def build_user_prompt(index: int, context: dict[str, Any], event: dict[str, Any]) -> str:
    payload = {
        "index": index + 1,
        "context": {
            "context_id": context.get("context_id"),
            "slot": context.get("slot"),
            "slot_time_hint": context.get("slot_time_hint"),
            "daily": context.get("daily"),
            "weather": context.get("weather"),
            "season": context.get("season"),
            "solar_term": context.get("solar_term"),
            "background": context.get("background"),
            "lifestyle_theme": context.get("lifestyle_theme"),
            "action_theme": context.get("action_theme"),
            "mood_theme": context.get("mood_theme"),
        },
        "event": {
            "event_id": event.get("event_id"),
            "context_id": event.get("context_id"),
            "slot": event.get("slot"),
            "title": event.get("title"),
            "summary": event.get("summary"),
            "triggered_by": event.get("triggered_by"),
            "text_to_image_prompt": event.get("text_to_image_prompt"),
        },
    }
    return (
        "请根据随消息附上的首帧图片，以及下面同一条内容的 A1 context 和 A2 event，"
        "生成一段用于 Seedance 的 15 秒图生视频中文 prompt。严格遵守 system prompt 的输出格式，"
        "注意：正式提交 Seedance 时还会额外传入一张蘑菇TUTU角色参考图，所以输出文本开头仍需写“第二张图片为参考图”。"
        "只输出一段中文提示词，不要解释，不要 JSON。\n\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
    )


def build_markdown(records: list[dict[str, Any]]) -> str:
    lines = ["# Phase C Seedance I2V Prompts", ""]
    for record in records:
        lines.extend(
            [
                f"## {record['index']:02d}. {record.get('title', '')}",
                "",
                f"- context_id: `{record.get('context_id', '')}`",
                f"- event_id: `{record.get('event_id', '')}`",
                f"- image: `{record.get('image_path', '')}`",
                "",
                record.get("seedance_i2v_prompt", "").strip(),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def run(args: argparse.Namespace) -> list[dict[str, Any]]:
    contexts = read_jsonl(args.contexts_jsonl)
    events = read_jsonl(args.events_jsonl)
    contexts_by_id = {record["context_id"]: record for record in contexts}
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_jsonl = output_dir / "phase_c_seedance_i2v_prompts.jsonl"
    output_md = output_dir / "phase_c_seedance_i2v_prompts.md"
    system_prompt = args.system_prompt.read_text(encoding="utf-8")

    done = load_done(output_jsonl)
    records = list(done.values())
    by_context_id = {record["context_id"]: record for record in records}

    selected_events = events[: args.limit] if args.limit else events
    for index, event in enumerate(selected_events):
        context_id = event["context_id"]
        if context_id in by_context_id and not args.force:
            print(f"[skip] {index + 1:02d} {context_id}")
            continue
        context = contexts_by_id[context_id]
        image_path = args.images_dir / f"{index + 1:05d}_01.png"
        if not image_path.exists():
            raise FileNotFoundError(f"Missing image for index {index + 1}: {image_path}")

        print(f"[run] {index + 1:02d} {context_id} {event.get('title', '')}")
        prompt = ""
        last_error: Exception | None = None
        for attempt in range(1, args.retries + 2):
            try:
                prompt = sanitize_seedance_prompt(strip_code_fence(
                    call_gemini_with_image(
                        system_prompt=system_prompt,
                        user_prompt=build_user_prompt(index, context, event),
                        first_frame_path=image_path,
                        timeout=args.timeout,
                    )
                ))
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                print(f"[retry] {index + 1:02d} attempt {attempt} failed: {exc}")
                if attempt <= args.retries:
                    time.sleep(args.retry_sleep)
        if not prompt:
            raise RuntimeError(f"Failed to generate prompt for {context_id}: {last_error}")

        record = {
            "index": index + 1,
            "context_id": context_id,
            "event_id": event.get("event_id"),
            "title": event.get("title"),
            "image_path": str(image_path),
            "text_to_image_prompt": event.get("text_to_image_prompt"),
            "seedance_i2v_prompt": prompt,
            "status": "succeeded",
        }
        by_context_id[context_id] = record
        records = sorted(by_context_id.values(), key=lambda item: int(item["index"]))
        write_jsonl(output_jsonl, records)
        output_md.write_text(build_markdown(records), encoding="utf-8")

    return sorted(by_context_id.values(), key=lambda item: int(item["index"]))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase C：首帧图 + A1/A2 生成 Seedance 图生视频 prompt")
    parser.add_argument("--contexts-jsonl", type=Path, default=DEFAULT_CONTEXTS)
    parser.add_argument("--events-jsonl", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--images-dir", type=Path, default=DEFAULT_IMAGES_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--system-prompt", type=Path, default=SYSTEM_PROMPT_PATH)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-sleep", type=float, default=5.0)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = run(args)
    print(
        json.dumps(
            {
                "count": len(records),
                "output_jsonl": str(args.output_dir / "phase_c_seedance_i2v_prompts.jsonl"),
                "output_md": str(args.output_dir / "phase_c_seedance_i2v_prompts.md"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
