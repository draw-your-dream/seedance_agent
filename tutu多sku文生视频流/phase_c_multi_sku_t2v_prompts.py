"""Phase C Multi-SKU T2V Prompts.

读 Phase B 产出的 blueprint（已经确定了 sku_index、三张参考图路径、标题以及上下文字段），
对每条 blueprint 调 Gemini，用 `phase_c_multi_sku_t2v_system_prompt.md` 作为 system prompt，
生成一段 5 秒单镜头的 Seedance T2V 中文 prompt。

含 sanitize（黑名单替换、时间码剥除）和 validate（首段开头、图片引用、五段标签）双层闸门，
失败自动重试 2 次。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


PIPELINE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PIPELINE_DIR / "outputs"

GEMINI_API_KEY = os.environ.get("PICAA_API_KEY") or os.environ.get("GEMINI_API_KEY") or "Nmqoo7UOx2z4PW6X7oNkUb8WRCZrDvwB"
GEMINI_URL = os.environ.get(
    "GEMINI_URL",
    "https://ai.ssnai.com/gemini/v1beta/models/gemini-3.1-pro-preview:generateContent",
)

DEFAULT_BLUEPRINTS = OUTPUT_DIR / "multi_sku_blueprints" / "phase_b_multi_sku_blueprints.jsonl"
DEFAULT_OUTPUT_DIR = OUTPUT_DIR / "multi_sku_t2v_prompts"
SYSTEM_PROMPT_PATH = PIPELINE_DIR / "phase_c_multi_sku_t2v_system_prompt.md"


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


def strip_code_fence(text: str) -> str:
    text = text.strip()
    fenced = re.search(r"```(?:text|markdown|json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    return text


FORBIDDEN_SUBSTR_REPLACEMENTS = {
    "以输入图片为第一帧": "",
    "第二张图片为参考图": "",
    "第一帧": "",
    "首帧": "",
    "4cm": "",
    "微缩尺度": "",
    "微小体量": "",
    "微小感": "",
    "不要超过画面三分之一": "",
    "不要超过画面一半": "",
    "不要超过画面的三分之一": "",
    "不要超过画面的一半": "",
    "切到POV": "",
    "切到 POV": "",
    "切到俯视": "",
    "切到近景": "",
    "切换镜头": "",
    "镜头切换": "",
    "分镜": "",
    "9:16": "",
    "16:9": "",
    "竖版": "",
    "横版": "",
    "竖构图": "",
    "横构图": "",
}


TIME_CODE_PATTERNS = [
    r"\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?\s*s",
    r"\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?\s*秒(?:钟)?",
    r"第\s*\d+(?:\.\d+)?\s*秒(?:钟)?",
    r"\d+(?:\.\d+)?\s*秒(?:钟)?(?=[：:])",
]


# 在场景段里强制张嘴 + 引用图片3 的死板模板句
FORCED_MOUTH_PATTERNS = [
    r"[，,]?\s*嘴巴[^，,。！？；,;!?]{0,6}微(?:微)?张开?[^，,。！？；,;!?]{0,20}(?:露出|按|参考)?\s*图片\s*3[^，,。！？；,;!?]{0,40}",
    r"[，,]?\s*嘴巴[^，,。！？；,;!?]{0,6}张开[^，,。！？；,;!?]{0,20}(?:露出|按|参考)\s*图片\s*3[^，,。！？；,;!?]{0,40}",
    r"[，,]?\s*嘴巴按\s*图片\s*3[^，,。！？；,;!?]{0,40}",
    r"[，,]?\s*露出\s*图片\s*3[^，,。！？；,;!?]{0,30}",
    r"[，,]?\s*按\s*图片\s*3\s*(?:中|里)?的\s*(?:嘴形|嘴唇|嘴内颜色)[^，,。！？；,;!?]{0,20}",
]


def strip_time_codes(text: str) -> str:
    for pattern in TIME_CODE_PATTERNS:
        text = re.sub(pattern, "", text)
    return text


def strip_forced_mouth(text: str) -> str:
    # 把首段"图片3 是...嘴巴解剖参考..."保留，只清理场景段里的强引用死句
    # 策略：只对首段后的部分应用
    paragraphs = text.split("\n\n")
    if not paragraphs:
        return text
    head, rest = paragraphs[0], paragraphs[1:]
    cleaned_rest = []
    for p in rest:
        for pattern in FORCED_MOUTH_PATTERNS:
            p = re.sub(pattern, "", p)
        cleaned_rest.append(p)
    return "\n\n".join([head] + cleaned_rest)


def sanitize_prompt(text: str) -> str:
    for old, new in FORBIDDEN_SUBSTR_REPLACEMENTS.items():
        text = text.replace(old, new)
    text = re.sub(r"生成\s*\d+\s*秒(?:钟)?", "", text)
    text = strip_time_codes(text)
    text = strip_forced_mouth(text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 清理连续逗号/句号
    text = re.sub(r"[，,]{2,}", "，", text)
    text = re.sub(r"[。]{2,}", "。", text)
    # 清理 strip_forced_mouth 留下的伪空句：":。" / "：。" / ":，" / "：，"
    text = re.sub(r"([：:])\s*[。，,]\s*", r"\1", text)
    text = re.sub(r"[。]\s*[，,]", "，", text)
    text = re.sub(r"[，,]\s*[。]", "。", text)
    return text.strip()


REQUIRED_SECTION_TAGS = ["风格：", "镜头：", "场景：", "配乐/音效：", "约束："]
REQUIRED_IMAGE_TOKENS = ["图片1", "图片2", "图片3"]


def validate_prompt_shape(prompt: str) -> list[str]:
    issues: list[str] = []
    if not prompt.lstrip().startswith("图片1是蘑菇TUTU的四视图"):
        issues.append("首段必须以\"图片1是蘑菇TUTU的四视图\"开头")
    for token in REQUIRED_IMAGE_TOKENS:
        if token not in prompt:
            issues.append(f"缺少图片引用：{token}")
    for tag in REQUIRED_SECTION_TAGS:
        if tag not in prompt:
            issues.append(f"缺少段落标签：{tag}")
    return issues


def resolve_default_blueprints(default: Path) -> Path:
    """If default path missing, pick the most recent outputs/multi_sku_blueprints/**/phase_b_multi_sku_blueprints.jsonl."""
    if default.exists():
        return default
    root = OUTPUT_DIR / "multi_sku_blueprints"
    if not root.exists():
        return default
    candidates = sorted(
        [p for p in root.rglob("phase_b_multi_sku_blueprints.jsonl") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else default


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
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        GEMINI_URL,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-goog-api-key": GEMINI_API_KEY,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini HTTP {exc.code}: {err_body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Gemini network error: {exc}") from exc
    try:
        response = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini returned non-JSON response: {body[:500]}") from exc
    if "error" in response:
        raise RuntimeError(json.dumps(response["error"], ensure_ascii=False))
    try:
        return response["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(
            "Gemini returned response without text: " + json.dumps(response, ensure_ascii=False)[:500]
        ) from exc


def build_user_prompt(blueprint: dict[str, Any]) -> str:
    context = blueprint.get("context") or {}
    payload = {
        "index": blueprint.get("index"),
        "sku_index": blueprint.get("sku_index"),
        "context": {
            "context_id": blueprint.get("context_id"),
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
        "请把下面这条 A1 context 改写成一条 5 秒 Seedance 文生视频 prompt。"
        "整段视频是一个连续镜头，不允许切镜；尺度通过周围物体与蘑菇TUTU 的具体大小对比来锁定；"
        "三张参考图分别是：图片1 蘑菇TUTU 四视图（SKU 编号见 sku_index），图片2 手和脚参考图，图片3 嘴巴参考图。"
        "请严格按 system prompt 的段落格式输出（首段图片关系 + 风格 + 镜头 + 场景 + 配乐/音效 + 约束）。"
        "只输出 prompt 文本，不要解释，不要 JSON。\n\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
    )


def build_markdown(records: list[dict[str, Any]]) -> str:
    lines = ["# Phase C Multi-SKU Seedance 5s T2V Prompts", ""]
    for record in records:
        lines.extend(
            [
                f"## {record['index']:02d}. {record.get('title', '')}",
                "",
                f"- context_id: `{record.get('context_id', '')}`",
                f"- sku_index: `{record.get('sku_index', '')}`",
                f"- sku_image: `{record.get('sku_image_path', '')}`",
                "",
                record.get("seedance_t2v_prompt", "").strip(),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def run(args: argparse.Namespace) -> list[dict[str, Any]]:
    blueprints_path = resolve_default_blueprints(args.blueprints_jsonl)
    if not blueprints_path.exists():
        raise FileNotFoundError(
            f"Phase B blueprint 文件不存在：{blueprints_path}\n"
            "请先运行 phase_b_multi_sku_blueprints.py 生成 blueprint，"
            "或用 --blueprints-jsonl 指向现有文件。"
        )
    blueprints = read_jsonl(blueprints_path)
    print(f"[blueprints] 读取：{blueprints_path} ({len(blueprints)} 条)")

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_jsonl = output_dir / "phase_c_multi_sku_t2v_prompts.jsonl"
    output_md = output_dir / "phase_c_multi_sku_t2v_prompts.md"
    system_prompt = args.system_prompt.read_text(encoding="utf-8")

    by_context_id = load_done(output_jsonl)
    selected = blueprints[: args.limit] if args.limit else blueprints
    for blueprint in selected:
        context_id = blueprint["context_id"]
        index = int(blueprint["index"])
        title = blueprint.get("title", "")
        sku_index = blueprint.get("sku_index")
        if context_id in by_context_id and not args.force:
            print(f"[skip] {index:02d} {context_id}")
            continue

        print(f"[run] {index:02d} {context_id} sku={sku_index} {title}")

        prompt = ""
        last_error: Exception | None = None
        for attempt in range(1, args.retries + 2):
            try:
                raw = call_gemini(
                    system_prompt=system_prompt,
                    user_prompt=build_user_prompt(blueprint),
                    timeout=args.timeout,
                )
                candidate = sanitize_prompt(strip_code_fence(raw))
                issues = validate_prompt_shape(candidate)
                if issues:
                    raise RuntimeError("shape issues: " + "; ".join(issues))
                prompt = candidate
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                print(f"[retry] {index:02d} attempt {attempt} failed: {exc}")
                if attempt <= args.retries:
                    time.sleep(args.retry_sleep)
        if not prompt:
            raise RuntimeError(f"Failed to generate prompt for {context_id}: {last_error}")

        by_context_id[context_id] = {
            "index": index,
            "context_id": context_id,
            "title": title,
            "sku_index": sku_index,
            "sku_image_path": blueprint.get("sku_image_path"),
            "hand_foot_image_path": blueprint.get("hand_foot_image_path"),
            "mouth_image_path": blueprint.get("mouth_image_path"),
            "seedance_t2v_prompt": prompt,
            "status": "succeeded",
        }
        records = sorted(by_context_id.values(), key=lambda item: int(item["index"]))
        write_jsonl(output_jsonl, records)
        output_md.write_text(build_markdown(records), encoding="utf-8")

    return sorted(by_context_id.values(), key=lambda item: int(item["index"]))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase C Multi-SKU: 读 Phase B blueprint，调 Gemini 生成 5 秒单镜头 Seedance T2V prompt",
    )
    parser.add_argument("--blueprints-jsonl", type=Path, default=DEFAULT_BLUEPRINTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--system-prompt", type=Path, default=SYSTEM_PROMPT_PATH)
    parser.add_argument("--limit", type=int, default=10)
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
                "output_jsonl": str(args.output_dir / "phase_c_multi_sku_t2v_prompts.jsonl"),
                "output_md": str(args.output_dir / "phase_c_multi_sku_t2v_prompts.md"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
