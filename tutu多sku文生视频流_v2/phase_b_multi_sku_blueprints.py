"""Phase B Multi-SKU Blueprints.

读 Phase A 产出的 A1 context，为每条 context 做确定性准备：
- 随机抽 1/7 种蘑菇TUTU SKU（可复现，靠 --seed 锁定）
- 解析三张参考图的本地路径（SKU 四视图 + 手脚 + 嘴巴）
- 根据 action_theme 派生标题

不调任何 LLM。产出 blueprint JSONL 供 Phase C 使用。
"""

from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path
from typing import Any


PIPELINE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PIPELINE_DIR / "outputs"

SKU_DIR = PIPELINE_DIR / "sku"
SKU_COUNT = 7
HAND_FOOT_IMAGE = SKU_DIR / "hand_foot.jpg"
MOUTH_IMAGE = SKU_DIR / "mouth.jpg"
BUTT_IMAGE = SKU_DIR / "屁股.png"

# SKU 名称 + 形象描述（写进 prompt 首段，且整段 prompt 里"蘑菇TUTU"前面都要带这个款式名）
SKU_REGISTRY: dict[int, dict[str, str]] = {
    1: {"name": "潜水款", "description": "蓝色的伞盖上有白色的斑点，头顶戴着一副粉色边框的泳镜，左侧别着一朵白色小花"},
    2: {"name": "冰晶款", "description": "浅蓝色的伞盖上有白色的斑点，右侧佩戴着一簇冰晶发饰"},
    3: {"name": "甜品款", "description": "黄色的伞盖上有深黄色的斑点，头顶顶着一撮白色的奶油和一颗红草莓"},
    4: {"name": "花花款", "description": "粉色的伞盖上有白色的斑点，头顶有一朵白花，花上停着一只黄色的蝴蝶"},
    5: {"name": "星月款", "description": "紫色的伞盖上有黄色的星星图案，头顶顶着一根融化的白色蜡烛"},
    6: {"name": "森林款", "description": "棕色的伞盖上有白色的斑点，头顶的绿叶上停着一只白色的小鸟"},
    7: {"name": "基础款", "description": "红色的伞盖上有白色的斑点"},
}

# 抽样权重：基础款 60%，其余 6 款合计 40%（平均每款 ≈6.67%）
SKU_WEIGHTS: dict[int, float] = {1: 40 / 6, 2: 40 / 6, 3: 40 / 6, 4: 40 / 6, 5: 40 / 6, 6: 40 / 6, 7: 60.0}

DEFAULT_CONTEXTS = OUTPUT_DIR / "phase_a" / "latest" / "phase_a_contexts.jsonl"
DEFAULT_OUTPUT_DIR = OUTPUT_DIR / "multi_sku_blueprints"

CONTEXT_FIELDS = [
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
]


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


def resolve_default_contexts(default: Path) -> Path:
    """If default path missing, pick the most recent outputs/phase_a/*/phase_a_contexts.jsonl."""
    if default.exists():
        return default
    phase_a_root = OUTPUT_DIR / "phase_a"
    if not phase_a_root.exists():
        return default
    candidates = sorted(
        [p for p in phase_a_root.glob("*/phase_a_contexts.jsonl") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else default


def title_from_context(context: dict[str, Any], index: int) -> str:
    action = str(context.get("action_theme") or context.get("lifestyle_theme") or "").strip()
    # 折叠空白，但保留全部内容和标点，方便人读
    action = re.sub(r"\s+", "", action)
    return action or f"multi_sku_t2v_{index + 1:05d}"


def pick_sku_index(rng: random.Random) -> int:
    indices = list(SKU_WEIGHTS.keys())
    weights = [SKU_WEIGHTS[i] for i in indices]
    return rng.choices(indices, weights=weights, k=1)[0]


def resolve_sku_path(sku_index: int) -> Path:
    if not (1 <= sku_index <= SKU_COUNT):
        raise ValueError(f"sku_index out of range: {sku_index}")
    path = SKU_DIR / f"{sku_index}.png"
    if not path.exists():
        raise FileNotFoundError(f"SKU image missing: {path}")
    return path


def build_blueprint(index: int, context: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    sku_index = pick_sku_index(rng)
    sku_path = resolve_sku_path(sku_index)
    sku_meta = SKU_REGISTRY[sku_index]
    sku_name = sku_meta["name"]
    sku_description = sku_meta["description"]
    sku_full_phrase = f"{sku_name}蘑菇TUTU，{sku_description}"
    title = title_from_context(context, index)
    context_subset = {field: context.get(field) for field in CONTEXT_FIELDS}
    return {
        "index": index + 1,
        "context_id": context.get("context_id"),
        "title": title,
        "sku_index": sku_index,
        "sku_name": sku_name,
        "sku_description": sku_description,
        "sku_full_phrase": sku_full_phrase,
        "sku_image_path": str(sku_path.resolve()),
        "hand_foot_image_path": str(HAND_FOOT_IMAGE.resolve()),
        "mouth_image_path": str(MOUTH_IMAGE.resolve()),
        "butt_image_path": str(BUTT_IMAGE.resolve()),
        "context": context_subset,
    }


def build_markdown(records: list[dict[str, Any]]) -> str:
    lines = ["# Phase B Multi-SKU Blueprints", ""]
    for record in records:
        lines.extend(
            [
                f"## {record['index']:02d}. {record.get('title', '')}",
                "",
                f"- context_id: `{record.get('context_id', '')}`",
                f"- sku_index: `{record.get('sku_index', '')}`",
                f"- sku_image: `{record.get('sku_image_path', '')}`",
                f"- hand_foot: `{record.get('hand_foot_image_path', '')}`",
                f"- mouth: `{record.get('mouth_image_path', '')}`",
                "",
                "```json",
                json.dumps(record.get("context", {}), ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def run(args: argparse.Namespace) -> list[dict[str, Any]]:
    contexts_path = resolve_default_contexts(args.contexts_jsonl)
    if not contexts_path.exists():
        raise FileNotFoundError(
            f"A1 context 文件不存在：{contexts_path}\n"
            "请先运行 phase_a.py 生成上下文，或用 --contexts-jsonl 指向现有文件。"
        )
    contexts = read_jsonl(contexts_path)
    print(f"[contexts] 读取：{contexts_path} ({len(contexts)} 条)")

    if not HAND_FOOT_IMAGE.exists():
        raise FileNotFoundError(f"Missing hand/foot reference: {HAND_FOOT_IMAGE}")
    if not MOUTH_IMAGE.exists():
        raise FileNotFoundError(f"Missing mouth reference: {MOUTH_IMAGE}")
    if not BUTT_IMAGE.exists():
        raise FileNotFoundError(f"Missing butt reference: {BUTT_IMAGE}")

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_jsonl = output_dir / "phase_b_multi_sku_blueprints.jsonl"
    output_md = output_dir / "phase_b_multi_sku_blueprints.md"

    rng = random.Random(args.seed)
    selected = contexts[: args.limit] if args.limit else contexts

    blueprints: list[dict[str, Any]] = []
    for index, context in enumerate(selected):
        blueprint = build_blueprint(index, context, rng)
        print(
            f"[blueprint] {blueprint['index']:02d} {blueprint['context_id']} "
            f"sku={blueprint['sku_index']} {blueprint['title']}"
        )
        blueprints.append(blueprint)

    write_jsonl(output_jsonl, blueprints)
    output_md.write_text(build_markdown(blueprints), encoding="utf-8")
    return blueprints


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase B Multi-SKU: 为每条 A1 context 随机选 SKU + 解析 3 张参考图路径 + 派生标题（无 LLM 调用）",
    )
    parser.add_argument("--contexts-jsonl", type=Path, default=DEFAULT_CONTEXTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=0, help="0=全部处理")
    parser.add_argument("--seed", type=int, default=20260424, help="SKU 随机种子，可复现")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    blueprints = run(args)
    print(
        json.dumps(
            {
                "count": len(blueprints),
                "output_jsonl": str(args.output_dir / "phase_b_multi_sku_blueprints.jsonl"),
                "output_md": str(args.output_dir / "phase_b_multi_sku_blueprints.md"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
