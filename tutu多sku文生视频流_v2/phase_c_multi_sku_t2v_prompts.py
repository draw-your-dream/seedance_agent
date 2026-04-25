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
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent",
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
    # 约束段第三项的回退兜底：模型常被前两句"X 保持一致"的对仗带偏，给图片3 也续成"保持一致"。
    "图片3 的嘴形和嘴内颜色保持一致": "参考图片3 嘴形和嘴内颜色",
    "图片3的嘴形和嘴内颜色保持一致": "参考图片3嘴形和嘴内颜色",
    # 标签纠偏：模型有时会写"配乐/音效："或"配乐："——统一收敛到"音效："
    "配乐/音效：": "音效：",
    "配乐/音效:": "音效：",
    "配乐：": "音效：",
    "配乐:": "音效：",
    # 严禁 BGM：剥掉模型偷偷夹带的背景音乐描述（先剥 musical 元素，再清理悬挂连接词由后续正则处理）
    # 注意：不能直接剥"背景音乐"——AUDIO_BAN_SENTENCE 里要保留这个词；只剥正向描述
    "轻柔的背景音乐": "",
    "轻快的背景音乐": "",
    "舒缓的背景音乐": "",
    "治愈的背景音乐": "",
    "温暖的背景音乐": "",
    "悠扬的背景音乐": "",
    "柔和的背景音乐": "",
    "悦耳的背景音乐": "",
    "BGM": "",
    "bgm": "",
    "钢琴曲": "",
    "吉他声": "",
    "小提琴声": "",
    "竖琴声": "",
    "口琴声": "",
    "音乐盒": "",
    "轻音乐": "",
    "纯音乐": "",
    "治愈系音乐": "",
    "治愈音乐": "",
    "舒缓的旋律": "",
    "轻柔的旋律": "",
    "温暖的旋律": "",
    "背景旋律": "",
    "旋律": "",
    "和弦": "",
    "节奏感": "",
    "曲调": "",
    "配乐": "",
    # 镜头不准抖：把"手持呼吸感"和各类晃动/来回摇摆描述整体抹掉
    "轻度手持呼吸感": "",
    "轻微手持呼吸感": "",
    "极轻微的手持呼吸感": "",
    "极轻微手持呼吸感": "",
    "手持呼吸感": "",
    "手持感": "",
    "画面微颤": "",
    "镜头微颤": "",
    "镜头颠簸": "",
    "镜头抖动": "",
    "轻微晃动": "",
    "左右晃动": "",
    "左右摇摆": "",
    "来回摇摆": "",
    "来回晃": "",
    "来回摆动": "",
    "镜头摇晃": "",
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
    # 把首段"图片3 是...表情参考..."保留，只清理场景段里的强引用死句
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
    # 清理 sanitize 剥词后悬挂的连接词：例如"无任何 X 或" / "X 或，" / "X 和。"
    text = re.sub(r"(或|和)\s*([。；;])", r"\2", text)
    text = re.sub(r"(或|和)\s*([，,])\s*", "，", text)
    text = re.sub(r"([，,])\s*(或|和)\s*([，,])", "，", text)
    # 修复"任何 X 或 Y"剥词后剩"任何或 Y"的情况（X 被 sanitize 删掉但留下了"或"+后续词）
    text = re.sub(r"(无|没有|杜绝|严禁|不允许|禁止)\s*任何\s*或\s*", r"\1任何", text)
    text = re.sub(r"任何\s*或\s*", "任何", text)
    # 强制兜底：音效段必须以"禁止背景音乐，只能有环境声和蘑菇TUTU 的声音。"结尾
    text = ensure_audio_ban(text)
    # 强制兜底：图片2 的描述统一规范化（不带 sku 前缀，写"毛绒粉色小手小脚"+"不准是圆柱形肉垫"）
    text = normalize_image2_sentence(text)
    return text.strip()


IMAGE2_CANONICAL = (
    "图片2是蘑菇TUTU的手和脚参考图，"
    "身体两侧的短手和下方的短腿末端都按图片2的毛绒粉色小手小脚来画，"
    "不要长手指不要长爪子，也不准是圆柱形肉垫。"
)


def normalize_image2_sentence(text: str) -> str:
    """把模型生成的"图片2是X款蘑菇TUTU的手和脚参考图..."一整句替换为规范版本。"""
    # 匹配从"图片2是"开始到第一个"。"为止的整句
    pattern = re.compile(r"图片2是[^。]*?手和脚[^。]*?。")
    return pattern.sub(IMAGE2_CANONICAL, text, count=1)


AUDIO_BAN_SENTENCE = "禁止背景音乐，只能有环境声和蘑菇TUTU 的声音。"


def ensure_audio_ban(text: str) -> str:
    """音效段如果没写禁 BGM 那句硬指令，自动补在末尾。"""
    paragraphs = text.split("\n\n")
    for i, p in enumerate(paragraphs):
        stripped = p.lstrip()
        if not stripped.startswith("音效："):
            continue
        # 已经包含禁背景音乐指令就跳过
        if "禁止背景音乐" in p:
            return text
        # 否则在段末追加
        p = p.rstrip()
        if not p.endswith(("。", "！", "？", ".", "!", "?")):
            p = p + "。"
        paragraphs[i] = p + AUDIO_BAN_SENTENCE
        return "\n\n".join(paragraphs)
    return text


REQUIRED_SECTION_TAGS = ["风格：", "镜头：", "场景：", "音效：", "约束："]
REQUIRED_IMAGE_TOKENS = ["图片1", "图片2", "图片3", "图片4"]


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
    sku_name = blueprint.get("sku_name", "")
    sku_full_phrase = blueprint.get("sku_full_phrase", "")
    payload = {
        "index": blueprint.get("index"),
        "sku_index": blueprint.get("sku_index"),
        "sku_name": sku_name,
        "sku_full_phrase": sku_full_phrase,
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
        "**音效段必须用'音效：'（不是'配乐：'）开头，只写真实环境声 + 蘑菇TUTU 的可爱拟声反应**，"
        "**且音效段最后一句必须原样写：'禁止背景音乐，只能有环境声和蘑菇TUTU 的声音。'**——这是给 Seedance 看的硬指令，不能省略或改写；"
        "整段视频是一个连续镜头，绝对不允许切镜、跳切、换机位；"
        f"尺度通过周围物体与{sku_name}蘑菇TUTU 的具体大小对比来锁定；"
        "**四张参考图**分别是：图片1 蘑菇TUTU 四视图（SKU 编号见 sku_index），图片2 手和脚参考图，图片3 表情参考图，**图片4 背面/屁股参考图（显示蘑菇TUTU 没有尾巴）**。"
        f"**首段必须把 sku_full_phrase（{sku_full_phrase}）原样拼到'图片1是蘑菇TUTU的四视图，'之后，且必须显式声明四张图片（图片1/图片2/图片3/图片4），其中图片4 必须明确说出'{sku_name}蘑菇TUTU 不能有尾巴'。**"
        f"**整段 prompt 里只要写'蘑菇TUTU'或'蘑菇'，前面必须带上 sku_name（{sku_name}），如'{sku_name}蘑菇TUTU'。**"
        "**整段 prompt 禁止出现任何关于尾巴的正向描写（尾根/尾椎/小尾巴/尾巴翘起/尾巴摆动/甩尾等），尾部只能以'无尾巴/没有尾巴/不长尾巴'形式出现。**"
        "请严格按 system prompt 的段落格式输出（首段图片关系 + 风格 + 镜头 + 场景 + 音效 + 约束）。"
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
