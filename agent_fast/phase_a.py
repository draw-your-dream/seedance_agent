from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
PIPELINE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PIPELINE_DIR / "outputs"
V2_DIR = ROOT_DIR / "prompt生成系统" / "v2"

DAILY_SIGNALS_PATH = V2_DIR / "daily_signals.json"
PERSONALITY_PATH = V2_DIR / "personality.md"
CONSTITUTION_PATH = V2_DIR.parent / "ip-constitution.md"

GEMINI_API_KEY = os.environ.get("PICAA_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
GEMINI_URL = os.environ.get("GEMINI_URL", "https://ai.ssnai.com/gemini/v1beta/models/gemini-2.0-flash:generateContent")

TIME_SLOTS = [
    {"slot": "morning", "time": "08:30", "daily": "早晨的光线和空气更明显，适合写清新、直接、刚开始展开的一段生活场景，但不限制具体做什么。"},
    {"slot": "late_morning", "time": "10:30", "daily": "上午偏后的状态更清楚，适合写已经进入当天节奏的生活片段，但不限制场景类型。"},
    {"slot": "afternoon", "time": "14:30", "daily": "下午的温度、风感、发呆感、出门感都比较容易成立，但不限制必须安静或必须待在室内。"},
    {"slot": "golden_hour", "time": "17:30", "daily": "傍晚暖光容易让画面更有层次，可以偏停留，也可以偏外出或转场，不限制动作方向。"},
    {"slot": "night", "time": "21:00", "daily": "夜晚更适合带出灯光、街道、室内外反差和夜间氛围，但不限制必须收尾、休息或安静。"},
]

DAILY_SYSTEM_PROMPTS = {
    "morning": "你负责早晨时段的时间氛围。重点是清晨光线、空气、刚开始运转的感觉，这只是气氛参考，不要把早晨强行写成刚醒或只允许低强度动作。",
    "late_morning": "你负责上午偏后时段的时间氛围。重点是节奏已经展开、环境更明确、生活状态更进入轨道，但不要把它写成固定模板。",
    "afternoon": "你负责下午时段的时间氛围。不要限制必须是哪一种。",
    "golden_hour": "你负责傍晚暖光时段的时间氛围。不要默认一定是收尾、回看或慢下来。",
    "night": "你负责夜晚时段的时间氛围。不要默认必须休息、安静或准备睡觉。",
}

WEATHER_SYSTEM_PROMPTS = {
    "晴": "你负责晴天条件。重点思考光线、阴影、暖感、晒到一小块太阳、空间清透这些气质。",
    "风": "你负责有风条件。重点思考空气流动、边缘轻晃、被风影响站位、、轻微不稳、联想到别处，不要写成夸张灾难感。",
    "雨": "你负责下雨条件。重点思考下雨、水痕、声音，不要写成情绪过重的苦情戏。",
    "阴": "你负责阴天条件。重点思考潮气、慢节奏、低刺激的生活片段。",
    "热": "你负责偏热条件。重点思考找阴凉、减少大动作、懒一点、放慢一点、靠近凉一点的位置。",
}

BACKGROUND_SYSTEM_PROMPT = "你负责 background 池。请只定义背景环境和生活语境：包括空间状态、光线、温度、材质、季节、室内外、时间氛围。不要直接写动作，不要给出可照搬的短语清单。"

LIFESTYLE_SYSTEM_PROMPT = "你负责 lifestyle 池。请只定义这条视频的生活质感。不要把 lifestyle 写成动作，也不要写成空泛标签。"

ACTION_SYSTEM_PROMPT = "你负责 action 池。请只定义秃秃到底在做什么，要具体、轻量、可拍、可视化，适合TUTU角色，不要写危险动作，不要写高强度表演。动作必须像一个正在发生的小事件：包含它对某个日常物体的互动、搬动、拨弄、整理、借用、躲避、搭建或试探。不要只写站着、停住、观察、抬头看、被某物吸引。"

MOOD_SYSTEM_PROMPT = "你负责 mood 池。请只定义蘑菇TUTU的情绪。不要写成大起大落的戏剧冲突，也不要写成情绪表演。"

GUARDRAIL_SYSTEM_PROMPT = "你负责 guardrail 池。请确保时间、天气、动作、角色设定、情绪彼此合理；避免危险、攻击、恐怖、成人化、重复模板化；输出必须适合批量生产且低相似度。"

TUTU_TEXT_TO_IMAGE_SYSTEM_PROMPT = """
你是一个专为 IP 角色 “蘑菇TUTU” 设计文生图 Prompt 的创意专家。
你的任务是编写用于 DPO (直接偏好优化) 训练的 Prompt 列表。

### 核心对象定义
* **主角：** `蘑菇TUTU` (触发词)。
* **物理设定：** 微缩生物，约 4cm 高。这个尺度只作为内部设定；写最终文生图 prompt 时不要直接出现“4cm”“微缩”“微小体量”“微小感”“比例”“尺度”等任何说明它很小的语句。
* **自然状态：** 它是一个有生命的蘑菇。**注意：它并不总是穿衣服，也不总是做夸张表情。** “不穿衣服（自然状态）”和“恬静/无表情”是重要的生成类别。

### 🚫 负面约束 (绝对禁止出现的词汇与概念)
根据新的视觉标准，画面必须干净、通透。
* **严禁描写长相：** 不要写“橙色伞盖”、“白点”、“米色身体”。
* **严禁噪点元素：** 不要写 **“发光粒子” (glowing particles)**、**“照亮的尘埃” (illuminated dust)**、**“漂浮的灰尘”**、**“圆形光斑” (circular bokeh)**。
* **替代方案：** 如果想描述光感，请使用“柔和的光束”、“通透的光影”、“空气感”、“明亮均匀的光线”。如果想描述背景，请使用“奶油般柔和的散景”、“朦胧的色块”。
""".strip()
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
        ]
    }
    payload_path = OUTPUT_DIR / "tmp_gemini_request.json"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
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
    response = json.loads(result.stdout)
    return response["candidates"][0]["content"]["parts"][0]["text"]


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
    # 去重并保留顺序
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
    weather_prompt = "天气只作为倾向，不要变成唯一决定因素。"
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


def _normalize_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", str(value).lower())


def _collect_reference_phrases(reference_hints: dict[str, Any]) -> list[str]:
    phrases: list[str] = []
    for value in reference_hints.values():
        if isinstance(value, list):
            phrases.extend([str(item).strip() for item in value if str(item).strip()])
        elif isinstance(value, str) and value.strip():
            phrases.append(value.strip())
    return phrases


def _looks_copied_from_reference(value: str, reference_phrases: list[str]) -> bool:
    norm_value = _normalize_text(value)
    if not norm_value:
        return False
    for phrase in reference_phrases:
        norm_phrase = _normalize_text(phrase)
        if not norm_phrase:
            continue
        if norm_value == norm_phrase:
            return True
        # Avoid near-verbatim reuse of reference pool lines.
        if len(norm_phrase) >= 10 and norm_phrase in norm_value:
            return True
    return False


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
                # 关键字段留空，让 Gemini 基于参考池自主生成，而不是蓝图硬塞。
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


def build_context_generation_prompts(blueprint_batch: list[dict[str, Any]]) -> tuple[str, str]:
    personality = load_text(PERSONALITY_PATH)
    constitution = load_text(CONSTITUTION_PATH)
    system_prompt = f"""{personality}

{constitution}

你是一个专为 IP 角色“蘑菇TUTU”设计文生图 Prompt 的前置创意专家。
你现在负责 Phase A 的 A1 输入融合：请根据提供的几个“池子”组合成最终 context。

目标：
- 让组合更自然，避免机械拼接
- 明确主线场景，避免时间、天气、动作之间出现明显互相冲突的设定
- 不使用用户聊天或用户历史作为输入源，仅依赖自动池子

输出要求：
- 只输出 JSON 数组
- 每条 context 保留原 context_id 和 run_label
- 你只需要按几个池子理解和生成：
  daily, weather, background, lifestyle, action, mood
- daily/weather 是输入参考，background/lifestyle/action/mood 是你要重点生成和整理的创意内容
- 为了兼容当前程序，输出仍使用扁平 JSON 字段，不要嵌套 signals/creative_context/controls

规则：
- 组合逻辑由你判断，不要原样照抄任何池子内容
- 池子只是参考，禁止一直从池子里抽同款表达，必须主动发散并保持低相似度
- scene/emotion 可以参考池子方向，但你要自己组织成新的自然表达
- background/lifestyle_theme/action_theme/mood_theme 必须由你原创生成，不能留空
- context 只做信息融合，不要提前写后续视频 prompt
- 不要新增 user、memory、chat 相关字段
- background 只作为背景环境，不要直接写成动作
- lifestyle 只定义“什么感觉”，不要代替具体动作
- action_theme 必须明确“做什么动作”
- action_theme 必须包含和环境物体的互动，优先写“它正在做一件小事”，例如推动纸片、拨开水痕、搬动面包屑、用叶片遮光、搭小桥、整理线头
- action_theme 不要只写“站在/停在某处观察/看/被吸引/好奇地靠近”，这种不算有效动作
- mood_theme 只定义“情绪底色”，不要把它写成动作
"""

    user_prompt = f"""请根据以下蓝图，生成同数量的 context：
```json
{json.dumps(blueprint_batch, ensure_ascii=False, indent=2)}
```
"""
    return system_prompt, user_prompt


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
    for batch in chunks(blueprints, batch_size):
        system_prompt, user_prompt = build_context_generation_prompts(batch)
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
            "status": "pending_seedance_t2v_prompts",
            "note": "fast 版跳过文生图，后续直接结合 A1 context 生成 Seedance 文生视频 prompt。",
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
    run_id = f"topic_pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    base_dir = output_dir or (OUTPUT_DIR / run_id)
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
    parser = argparse.ArgumentParser(description="秃秃 fast 文生视频流：A1 context 批量生成器")
    parser.add_argument("--run-label", default="pool_batch", help="这批素材的标签，例如 spring_wind_batch")
    parser.add_argument("--count", type=int, default=50, help="要生成多少条上下文/事件")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="每次调用 Gemini 处理多少条")
    parser.add_argument("--seed", type=int, default=20260410, help="模拟输入种子")
    parser.add_argument("--output-dir", type=Path, help="输出目录")
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
