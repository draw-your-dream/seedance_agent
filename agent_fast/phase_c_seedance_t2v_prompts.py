from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any


PIPELINE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PIPELINE_DIR / "outputs"

GEMINI_API_KEY = os.environ.get("PICAA_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
GEMINI_URL = os.environ.get(
    "GEMINI_URL",
    "https://ai.ssnai.com/gemini/v1beta/models/gemini-3.1-pro-preview:generateContent",
)

DEFAULT_CONTEXTS = OUTPUT_DIR / "gemini_25_flash_50_b25" / "phase_a_contexts.jsonl"
DEFAULT_OUTPUT_DIR = OUTPUT_DIR / "seedance_t2v_prompts_gemini_31_pro_preview_50"
SYSTEM_PROMPT_PATH = PIPELINE_DIR / "phase_c_seedance_t2v_system_prompt.md"


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


def strip_code_fence(text: str) -> str:
    text = text.strip()
    fenced = re.search(r"```(?:text|markdown|json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    return text


def sanitize_seedance_prompt(text: str) -> str:
    replacements = {
        "以输入图片为第一帧，第二张图片为参考图，保持整体构图、透视关系与空间结构一致": "以参考图中的蘑菇TUTU作为唯一角色参考，保持角色外观特征一致",
        "以输入图片为第一帧": "以参考图中的蘑菇TUTU作为唯一角色参考",
        "第二张图片为参考图": "参考图中的蘑菇TUTU作为角色参考",
        "首帧": "开场画面",
        "小短手": "双手",
        "小短腿": "腿",
        "小身体": "身体",
        "小手": "手",
        "小脚": "脚",
        "4cm": "",
        "微缩": "",
        "微小体量": "",
        "微小感": "",
        "比例": "",
        "尺度": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"\s+", " ", text).strip()
    if not text.startswith("以参考图中的蘑菇TUTU作为唯一角色参考"):
        text = "以参考图中的蘑菇TUTU作为唯一角色参考，保持角色外观特征一致，" + text
    return text


def title_from_context(context: dict[str, Any], index: int) -> str:
    action = str(context.get("action_theme") or context.get("lifestyle_theme") or "").strip()
    action = re.sub(r"[，。！？、\s]+", "", action)
    return action[:16] or f"fast_t2v_{index + 1:05d}"


def load_done(path: Path) -> dict[str, dict[str, Any]]:
    done: dict[str, dict[str, Any]] = {}
    for record in read_jsonl(path):
        context_id = str(record.get("context_id", ""))
        prompt = str(record.get("seedance_t2v_prompt", "")).strip()
        if context_id and prompt and record.get("status") == "succeeded":
            done[context_id] = record
    return done


def call_gemini(system_prompt: str, user_prompt: str, timeout: int) -> str:
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": f"{system_prompt}\n\n---\n\n{user_prompt}"}],
            }
        ],
        "generationConfig": {
            "temperature": 0.85,
            "topP": 0.95,
        },
    }
    tmp_dir = OUTPUT_DIR / "_tmp_phase_c_fast"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    payload_path = tmp_dir / "gemini_t2v_request.json"
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


def build_user_prompt(index: int, context: dict[str, Any]) -> str:
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
    }
    return (
        "请把下面这条 A1 context 改写成 Seedance 文生视频 prompt。"
        "这条 fast 链路不生成文生图 prompt，不生成首帧图，也不做图生视频。"
        "只输出一段中文提示词，不要解释，不要 JSON。\n\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
    )


def build_markdown(records: list[dict[str, Any]]) -> str:
    lines = ["# Phase C Fast Seedance T2V Prompts", ""]
    for record in records:
        lines.extend(
            [
                f"## {record['index']:02d}. {record.get('title', '')}",
                "",
                f"- context_id: `{record.get('context_id', '')}`",
                "",
                record.get("seedance_t2v_prompt", "").strip(),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def run(args: argparse.Namespace) -> list[dict[str, Any]]:
    contexts = read_jsonl(args.contexts_jsonl)
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_jsonl = output_dir / "phase_c_seedance_t2v_prompts.jsonl"
    output_md = output_dir / "phase_c_seedance_t2v_prompts.md"
    system_prompt = args.system_prompt.read_text(encoding="utf-8")

    by_context_id = load_done(output_jsonl)
    selected_contexts = contexts[: args.limit] if args.limit else contexts
    for index, context in enumerate(selected_contexts):
        context_id = context["context_id"]
        title = title_from_context(context, index)
        if context_id in by_context_id and not args.force:
            print(f"[skip] {index + 1:02d} {context_id}")
            continue
        print(f"[run] {index + 1:02d} {context_id} {title}")

        prompt = ""
        last_error: Exception | None = None
        for attempt in range(1, args.retries + 2):
            try:
                prompt = sanitize_seedance_prompt(strip_code_fence(call_gemini(
                    system_prompt=system_prompt,
                    user_prompt=build_user_prompt(index, context),
                    timeout=args.timeout,
                )))
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                print(f"[retry] {index + 1:02d} attempt {attempt} failed: {exc}")
                if attempt <= args.retries:
                    time.sleep(args.retry_sleep)
        if not prompt:
            raise RuntimeError(f"Failed to generate prompt for {context_id}: {last_error}")

        by_context_id[context_id] = {
            "index": index + 1,
            "context_id": context_id,
            "title": title,
            "seedance_t2v_prompt": prompt,
            "status": "succeeded",
        }
        records = sorted(by_context_id.values(), key=lambda item: int(item["index"]))
        write_jsonl(output_jsonl, records)
        output_md.write_text(build_markdown(records), encoding="utf-8")

    return sorted(by_context_id.values(), key=lambda item: int(item["index"]))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fast Phase C：A1 context 直接生成 Seedance 文生视频 prompt")
    parser.add_argument("--contexts-jsonl", type=Path, default=DEFAULT_CONTEXTS)
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
                "output_jsonl": str(args.output_dir / "phase_c_seedance_t2v_prompts.jsonl"),
                "output_md": str(args.output_dir / "phase_c_seedance_t2v_prompts.md"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
