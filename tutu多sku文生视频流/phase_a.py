from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PIPELINE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PIPELINE_DIR / "outputs"
IP_DATA_DIR = PIPELINE_DIR / "ip_data"

DAILY_SIGNALS_PATH = IP_DATA_DIR / "daily_signals.json"
PERSONALITY_PATH = IP_DATA_DIR / "personality.md"
CONSTITUTION_PATH = IP_DATA_DIR / "ip-constitution.md"

PHASE_A_SYSTEM_PROMPT_PATH = PIPELINE_DIR / "phase_a_system_prompt.md"
PHASE_A_POOL_SUBPROMPTS_PATH = PIPELINE_DIR / "phase_a_pool_subprompts.md"

GEMINI_API_KEY = os.environ.get("PICAA_API_KEY") or os.environ.get("GEMINI_API_KEY") or "Nmqoo7UOx2z4PW6X7oNkUb8WRCZrDvwB"
GEMINI_URL = os.environ.get(
    "GEMINI_URL",
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent",
)

TIME_SLOTS = [
    {"slot": "morning", "time": "08:30", "daily": "早晨的光线和空气更明显，适合写清新、直接、刚开始展开的一段生活场景，但不限制具体做什么。"},
    {"slot": "late_morning", "time": "10:30", "daily": "上午偏后的状态更清楚，适合写已经进入当天节奏的生活片段，但不限制场景类型。"},
    {"slot": "afternoon", "time": "14:30", "daily": "下午的温度、风感、发呆感、出门感都比较容易成立，但不限制必须安静或必须待在室内。"},
    {"slot": "golden_hour", "time": "17:30", "daily": "傍晚暖光容易让画面更有层次，可以偏停留，也可以偏外出或转场，不限制动作方向。"},
    {"slot": "night", "time": "21:00", "daily": "夜晚更适合带出灯光、街道、室内外反差和夜间氛围，但不限制必须收尾、休息或安静。"},
]

def parse_pool_subprompts(md_text: str) -> dict[str, Any]:
    """Parse `## pool` / `### key` markdown into a dict.

    - Drops everything before the first horizontal rule (`---`) so leading prose is ignored.
    - A pool with `### key` sub-sections becomes dict[str, str].
    - A pool without sub-sections becomes a plain str.
    """
    if "\n---" in md_text:
        md_text = md_text.split("\n---", 1)[1]

    result: dict[str, Any] = {}
    current_pool: str | None = None
    current_key: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        if current_pool is None:
            buf = []
            return
        content = "\n".join(buf).strip()
        buf = []
        if not content:
            return
        if current_key is not None:
            bucket = result.setdefault(current_pool, {})
            if isinstance(bucket, dict):
                bucket[current_key] = content
        else:
            result[current_pool] = content

    for raw in md_text.splitlines():
        m2 = re.match(r"^##\s+(.+?)\s*$", raw)
        m3 = re.match(r"^###\s+(.+?)\s*$", raw)
        if m2 and not raw.startswith("###"):
            flush()
            current_pool = m2.group(1).strip()
            current_key = None
        elif m3:
            flush()
            current_key = m3.group(1).strip()
        else:
            if current_pool is not None:
                buf.append(raw)
    flush()
    return result


_POOL_SUBPROMPTS = parse_pool_subprompts(PHASE_A_POOL_SUBPROMPTS_PATH.read_text(encoding="utf-8"))

DAILY_SYSTEM_PROMPTS: dict[str, str] = _POOL_SUBPROMPTS["daily"]
WEATHER_SYSTEM_PROMPTS: dict[str, str] = _POOL_SUBPROMPTS["weather"]
WEATHER_DEFAULT_PROMPT: str = _POOL_SUBPROMPTS["weather_default"]
BACKGROUND_SYSTEM_PROMPT: str = _POOL_SUBPROMPTS["background"]
LIFESTYLE_SYSTEM_PROMPT: str = _POOL_SUBPROMPTS["lifestyle"]
ACTION_SYSTEM_PROMPT: str = _POOL_SUBPROMPTS["action"]
MOOD_SYSTEM_PROMPT: str = _POOL_SUBPROMPTS["mood"]
GUARDRAIL_SYSTEM_PROMPT: str = _POOL_SUBPROMPTS["guardrail"]

_PHASE_A_SYSTEM_PROMPT_TEMPLATE = PHASE_A_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
if "\n---" in _PHASE_A_SYSTEM_PROMPT_TEMPLATE:
    _PHASE_A_SYSTEM_PROMPT_TEMPLATE = _PHASE_A_SYSTEM_PROMPT_TEMPLATE.split("\n---", 1)[1].lstrip()

SIMULATED_WEATHERS = [
    {"weather": "晴，有风，24度", "season": "晚春", "solar_term": "清明后"},
    {"weather": "晴，微风，27度", "season": "初夏", "solar_term": "立夏前后"},
    {"weather": "多云，闷热，29度", "season": "初夏", "solar_term": "立夏后"},
    {"weather": "小雨，微凉，19度", "season": "春末", "solar_term": "谷雨前"},
    {"weather": "阴天，潮潮的，22度", "season": "梅雨前", "solar_term": "小满前"},
]

TRIGGER_PRIORITY_DEFAULT = ["action", "mood", "lifestyle", "weather", "daily", "background"]

DEFAULT_BATCH_SIZE = 10


@dataclass
class RunArtifacts:
    run_id: str
    output_dir: Path
    contexts_path: Path
    manifest_path: Path


def build_context_id(run_label: str, index: int) -> str:
    safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", run_label).strip("_") or "batch"
    return f"ctx_{safe_label}_{index + 1:05d}"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def parse_json_block(text: str, default: Any) -> Any:
    if not text:
        return default

    fenced = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)

    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for pattern in (r"\[\s*{.*}\s*\]", r"\{.*\}"):
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                continue
    return default


def call_gemini(system_prompt: str, user_prompt: str, timeout: int = 180) -> str:
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": f"{system_prompt}\n\n---\n\n{user_prompt}"}],
            }
        ],
        "generationConfig": {
            "temperature": 0.9,
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


def chunks(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[idx: idx + size] for idx in range(0, len(items), size)]


def load_background_pool() -> tuple[list[str], list[dict[str, Any]]]:
    records = load_json(DAILY_SIGNALS_PATH, [])
    if not records:
        return [], []
    topics: list[str] = []
    signals: list[dict[str, Any]] = []
    for record in records:
        topics.extend([t for t in record.get("hot_topics", []) if isinstance(t, str)])
        for signal in record.get("signals", []):
            if isinstance(signal, dict):
                signals.append(signal)
    uniq_topics = list(dict.fromkeys(topics))
    uniq_signals = []
    seen = set()
    for sig in signals:
        key = (sig.get("source_topic", ""), sig.get("scene", ""))
        if key in seen:
            continue
        seen.add(key)
        uniq_signals.append(sig)
    return uniq_topics, uniq_signals


def build_reference_catalog(slot: str, weather_text: str) -> dict[str, Any]:
    weather_prompt = WEATHER_DEFAULT_PROMPT
    for keyword, prompt in WEATHER_SYSTEM_PROMPTS.items():
        if keyword in weather_text:
            weather_prompt = prompt
            break

    return {
        "principle": [
            "池子已经不再是素材列表，而是分模块 system prompt。",
            "禁止把任何参考内容当成可直接摘抄的句子来源。",
            "最终内容必须由 Gemini 自主发散生成，并保持低相似度。",
        ],
        "daily_system_prompt": DAILY_SYSTEM_PROMPTS[slot],
        "weather_system_prompt": weather_prompt,
        "background_system_prompt": BACKGROUND_SYSTEM_PROMPT,
        "lifestyle_system_prompt": LIFESTYLE_SYSTEM_PROMPT,
        "action_system_prompt": ACTION_SYSTEM_PROMPT,
        "mood_system_prompt": MOOD_SYSTEM_PROMPT,
        "guardrail_system_prompt": GUARDRAIL_SYSTEM_PROMPT,
    }


def build_context_fallback(blueprint: dict[str, Any]) -> dict[str, Any]:
    slot = blueprint.get("slot", "daytime")
    weather = blueprint.get("weather", "当下天气")
    season = blueprint.get("season", "当季")
    slot_defaults = {
        "morning": {
            "background": "晨间光线下的生活场景",
            "lifestyle_theme": "轻亮、开始展开的生活感",
            "action_theme": "围绕一个日常小物完成轻微互动，例如拖动、拨开、扶住、整理或借用它",
            "mood_theme": "清爽里带一点主动性",
        },
        "late_morning": {
            "background": "上午已进入状态的生活场景",
            "lifestyle_theme": "自然展开、不慌不忙的生活感",
            "action_theme": "围绕眼前小物做一个短而完整的小任务，例如推、摆、探、擦、搭或搬",
            "mood_theme": "稳定里带一点兴致",
        },
        "afternoon": {
            "background": "下午空气感生活场景",
            "lifestyle_theme": "松弛、可内可外的下午生活感",
            "action_theme": "顺着环境线索和日常物体发生互动，动作要清楚可见，而不是只停留观看",
            "mood_theme": "放松里带一点游离和兴趣",
        },
        "golden_hour": {
            "background": "傍晚暖光生活场景",
            "lifestyle_theme": "有光线层次和过渡感的生活感",
            "action_theme": "借着暖光或空间层次做一个短而完整的动作段落",
            "mood_theme": "柔和里带一点认真和试探感",
        },
        "night": {
            "background": "夜间灯光下的生活场景",
            "lifestyle_theme": "带一点夜间流动感的生活感",
            "action_theme": "借用灯光、街道或室内外反差，完成一个可见的小互动动作",
            "mood_theme": "夜色里带一点兴致或松弛感",
        },
    }
    weather_suffix = "环境里有轻微变化"
    if "风" in weather:
        weather_suffix = "空气在流动，边缘有一点轻晃"
    elif "雨" in weather:
        weather_suffix = "周围带着一点潮湿和收住的感觉"
    elif "晴" in weather:
        weather_suffix = "光线比较清楚，也有明暗变化"
    elif "阴" in weather:
        weather_suffix = "整体偏柔和，节奏也更慢一点"
    elif "热" in weather or "闷" in weather:
        weather_suffix = "温度偏高，更适合减少动作幅度"

    defaults = slot_defaults.get(
        slot,
        {
            "background": "日常生活场景",
            "lifestyle_theme": "自然、不用力的生活感",
            "action_theme": "围绕身边小物完成一个轻巧、明确、可拍的小互动动作",
            "mood_theme": "轻微、克制的情绪状态",
        },
    )
    return {
        **blueprint,
        "background": f"{season}的日常角落里，{weather_suffix}，{defaults['background']}",
        "lifestyle_theme": defaults["lifestyle_theme"],
        "action_theme": defaults["action_theme"],
        "mood_theme": defaults["mood_theme"],
    }


def simulate_fused_context_blueprints(run_label: str, count: int, seed: int) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for idx in range(count):
        slot_info = TIME_SLOTS[idx % len(TIME_SLOTS)]
        weather = SIMULATED_WEATHERS[idx % len(SIMULATED_WEATHERS)]
        reference_hints = build_reference_catalog(slot_info["slot"], weather["weather"])

        contexts.append(
            {
                "context_id": build_context_id(run_label, idx),
                "run_label": run_label,
                "slot": slot_info["slot"],
                "slot_time_hint": slot_info["time"],
                "daily": slot_info["daily"],
                "weather": weather["weather"],
                "season": weather["season"],
                "solar_term": weather["solar_term"],
                "background": "",
                "lifestyle_theme": "",
                "action_theme": "",
                "mood_theme": "",
                "trigger_priority": TRIGGER_PRIORITY_DEFAULT,
                "pool_usage_policy": "reference_only_do_not_copy",
                "reference_hints": reference_hints,
            }
        )
    return contexts


def build_context_generation_prompts(
    blueprint_batch: list[dict[str, Any]],
    previous_themes: list[str] | None = None,
) -> tuple[str, str]:
    personality = load_text(PERSONALITY_PATH)
    constitution = load_text(CONSTITUTION_PATH)
    system_prompt = (
        _PHASE_A_SYSTEM_PROMPT_TEMPLATE
        .replace("{personality}", personality)
        .replace("{constitution}", constitution)
    )
    history_block = ""
    if previous_themes:
        # 取最近 80 条作为反例（兼顾上下文长度），太长容易让 Gemini 忽略
        recent = previous_themes[-80:]
        history_block = (
            "**已生成过的 action_theme 历史（反例，本次输出严禁与下面任何一条在主动词或互动物体上重复）**：\n"
            + "\n".join(f"- {t}" for t in recent)
            + "\n\n"
        )
    user_prompt = (
        f"{history_block}"
        "请根据以下蓝图，生成同数量的 context：\n"
        "```json\n"
        f"{json.dumps(blueprint_batch, ensure_ascii=False, indent=2)}\n"
        "```\n"
    )
    return system_prompt, user_prompt


def load_history_action_themes() -> list[str]:
    """扫历史 phase_a 输出，收集所有曾经生成过的 action_theme，作为防重复反例。"""
    out: list[str] = []
    seen: set[str] = set()
    phase_a_root = OUTPUT_DIR / "phase_a"
    if not phase_a_root.exists():
        return out
    for path in sorted(phase_a_root.glob("*/phase_a_contexts.jsonl"), key=lambda p: p.stat().st_mtime):
        try:
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                theme = str(rec.get("action_theme", "")).strip()
                if theme and theme not in seen:
                    seen.add(theme)
                    out.append(theme)
        except Exception:
            continue
    return out


def validate_context(context: dict[str, Any]) -> None:
    required = [
        "context_id",
        "run_label",
        "slot",
        "slot_time_hint",
        "daily",
        "weather",
        "season",
        "solar_term",
        "background",
        "lifestyle_theme",
        "action_theme",
        "mood_theme",
        "trigger_priority",
        "reference_hints",
    ]
    for key in required:
        if key not in context:
            raise ValueError(f"context 缺字段: {key}")
    generated_keys = ["background", "lifestyle_theme", "action_theme", "mood_theme"]
    for key in generated_keys:
        if not str(context.get(key, "")).strip():
            raise ValueError(f"context 字段为空，需由 Gemini 生成: {key}")


def generate_contexts_with_gemini(blueprints: list[dict[str, Any]], batch_size: int) -> list[dict[str, Any]]:
    all_contexts: list[dict[str, Any]] = []
    history_themes = load_history_action_themes()
    print(f"[phase-a] loaded {len(history_themes)} history action_themes as anti-repetition examples", flush=True)
    for batch in chunks(blueprints, batch_size):
        # 把历史 + 本轮已生成的合在一起作为反例
        seen_themes = list(history_themes) + [c.get("action_theme", "") for c in all_contexts]
        system_prompt, user_prompt = build_context_generation_prompts(batch, previous_themes=seen_themes)
        raw = call_gemini(system_prompt, user_prompt)
        parsed = parse_json_block(raw, default=[])
        if not isinstance(parsed, list):
            parsed = []

        by_id = {item["context_id"]: item for item in batch}
        batch_contexts: list[dict[str, Any]] = []
        for item in parsed:
            context_id = item.get("context_id", "")
            fallback = by_id.get(context_id)
            if not fallback:
                continue
            if not item.get("background"):
                legacy_background = [
                    str(item.get("background_env", "")).strip(),
                    str(item.get("background_topic", "")).strip(),
                    str(item.get("background_reason", "")).strip(),
                ]
                item["background"] = "；".join(part for part in legacy_background if part)
            merged = dict(fallback)
            for key in merged.keys():
                if key in item and item[key] not in (None, ""):
                    merged[key] = item[key]
            validate_context(merged)
            batch_contexts.append(merged)

        returned_ids = {ctx["context_id"] for ctx in batch_contexts}
        for fallback in batch:
            if fallback["context_id"] not in returned_ids:
                fallback_context = build_context_fallback(fallback)
                validate_context(fallback_context)
                batch_contexts.append(fallback_context)

        all_contexts.extend(batch_contexts)
    return all_contexts


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_manifest(
    path: Path,
    run_label: str,
    count: int,
    batch_size: int,
    contexts_path: Path,
) -> None:
    payload = {
        "run_label": run_label,
        "count": count,
        "batch_size": batch_size,
        "phase_a_outputs": {
            "contexts_jsonl": str(contexts_path),
        },
        "phase_c_seedance_outputs": {
            "status": "pending_phase_c_multi_sku_t2v_prompts",
            "note": "本 pipeline 跳过文生图，后续直接结合 A1 context 生成 5 秒单镜头 Seedance 文生视频 prompt。",
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_pipeline(
    run_label: str,
    count: int,
    batch_size: int,
    seed: int,
    output_dir: Path | None,
) -> RunArtifacts:
    run_id = f"phase_a_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    base_dir = output_dir or (OUTPUT_DIR / "phase_a" / run_id)
    base_dir.mkdir(parents=True, exist_ok=True)

    blueprints = simulate_fused_context_blueprints(run_label=run_label, count=count, seed=seed)
    contexts = generate_contexts_with_gemini(blueprints, batch_size=batch_size)

    contexts_path = base_dir / "phase_a_contexts.jsonl"
    manifest_path = base_dir / "manifest.json"

    write_jsonl(contexts_path, contexts)

    write_manifest(
        manifest_path,
        run_label=run_label,
        count=count,
        batch_size=batch_size,
        contexts_path=contexts_path,
    )

    return RunArtifacts(
        run_id=run_id,
        output_dir=base_dir,
        contexts_path=contexts_path,
        manifest_path=manifest_path,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase A：A1 context 批量生成器（本目录自包含版）")
    parser.add_argument("--run-label", default="multi_sku_batch", help="这批素材的标签，例如 spring_wind_batch")
    parser.add_argument("--count", type=int, default=50, help="要生成多少条上下文/事件")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="每次调用 Gemini 处理多少条")
    parser.add_argument("--seed", type=int, default=20260424, help="模拟输入种子")
    parser.add_argument("--output-dir", type=Path, help="输出目录；默认 outputs/phase_a/<timestamp>/")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    artifacts = run_pipeline(
        run_label=args.run_label,
        count=args.count,
        batch_size=args.batch_size,
        seed=args.seed,
        output_dir=args.output_dir,
    )

    summary = {
        "run_id": artifacts.run_id,
        "output_dir": str(artifacts.output_dir),
        "phase_a_contexts": str(artifacts.contexts_path),
        "manifest": str(artifacts.manifest_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
