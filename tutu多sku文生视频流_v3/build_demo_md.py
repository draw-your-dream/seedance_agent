"""根据 Phase A / B / C 的 jsonl 产物，生成一份对齐展示的 md 文档。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PIPELINE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PIPELINE_DIR / "outputs"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def find_latest(pattern: str) -> Path | None:
    candidates = sorted(
        [p for p in OUTPUT_DIR.rglob(pattern) if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def build_md(
    phase_a: list[dict[str, Any]],
    phase_b: list[dict[str, Any]],
    phase_c: list[dict[str, Any]],
) -> str:
    by_id_a = {r["context_id"]: r for r in phase_a}
    by_id_b = {r["context_id"]: r for r in phase_b}
    by_id_c = {r["context_id"]: r for r in phase_c}

    ids = [r["context_id"] for r in phase_c]

    lines: list[str] = []
    lines.append("# TUTU Multi-SKU 5s Pipeline — 50 条完整样本对齐")
    lines.append("")
    lines.append(
        f"本文档由 `build_demo_md.py` 自动生成，把同一批 50 条 context 在 "
        f"**Phase A（A1 context）/ Phase B（SKU 分配）/ Phase C（最终 T2V prompt）** "
        f"三个阶段的数据按 `context_id` 对齐展示。"
    )
    lines.append("")
    lines.append("## 统计")
    lines.append("")
    lines.append(f"- Phase A 产出：{len(phase_a)} 条 A1 context")
    lines.append(f"- Phase B 产出：{len(phase_b)} 条 blueprint")
    lines.append(f"- Phase C 产出：{len(phase_c)} 条 T2V prompt")
    lines.append("")

    # SKU distribution
    sku_count: dict[int, int] = {}
    for r in phase_b:
        sku_count[int(r.get("sku_index", 0))] = sku_count.get(int(r.get("sku_index", 0)), 0) + 1
    lines.append("### SKU 分布")
    lines.append("")
    lines.append("| SKU | 次数 |")
    lines.append("|-----|------|")
    for sku in sorted(sku_count):
        lines.append(f"| {sku} | {sku_count[sku]} |")
    lines.append("")

    # Slot distribution
    slot_count: dict[str, int] = {}
    for r in phase_a:
        slot_count[r.get("slot", "?")] = slot_count.get(r.get("slot", "?"), 0) + 1
    lines.append("### 时段分布")
    lines.append("")
    lines.append("| slot | 次数 |")
    lines.append("|------|------|")
    for slot, n in slot_count.items():
        lines.append(f"| {slot} | {n} |")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 50 条样本详情")
    lines.append("")

    for index, cid in enumerate(ids, start=1):
        a = by_id_a.get(cid, {})
        b = by_id_b.get(cid, {})
        c = by_id_c.get(cid, {})
        title = c.get("title") or b.get("title") or cid

        lines.append(f"### {index:02d}. {title}")
        lines.append("")
        lines.append(f"- **context_id**: `{cid}`")
        lines.append(f"- **sku_index**: `{b.get('sku_index', '-')}`（用到 `sku/{b.get('sku_index', '?')}.png` 作为图片1）")
        lines.append("")

        lines.append("#### Phase A — A1 Context")
        lines.append("")
        lines.append("```json")
        a_display = {
            "context_id": a.get("context_id"),
            "slot": a.get("slot"),
            "slot_time_hint": a.get("slot_time_hint"),
            "weather": a.get("weather"),
            "season": a.get("season"),
            "solar_term": a.get("solar_term"),
            "daily": a.get("daily"),
            "background": a.get("background"),
            "lifestyle_theme": a.get("lifestyle_theme"),
            "action_theme": a.get("action_theme"),
            "mood_theme": a.get("mood_theme"),
        }
        lines.append(json.dumps(a_display, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")

        lines.append("#### Phase B — Blueprint（SKU + 参考图路径 + 标题）")
        lines.append("")
        b_display = {
            "index": b.get("index"),
            "context_id": b.get("context_id"),
            "title": b.get("title"),
            "sku_index": b.get("sku_index"),
            "sku_image_path": b.get("sku_image_path"),
            "hand_foot_image_path": b.get("hand_foot_image_path"),
            "mouth_image_path": b.get("mouth_image_path"),
        }
        lines.append("```json")
        lines.append(json.dumps(b_display, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")

        lines.append("#### Phase C — 最终送给 Seedance 的 5 秒 T2V Prompt")
        lines.append("")
        prompt = c.get("seedance_t2v_prompt", "").strip() or "（生成失败）"
        lines.append(prompt)
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="对齐 Phase A/B/C 产物，生成展示 md")
    parser.add_argument("--phase-a", type=Path, help="Phase A jsonl，默认自动找最近")
    parser.add_argument("--phase-b", type=Path, help="Phase B jsonl，默认自动找最近")
    parser.add_argument("--phase-c", type=Path, help="Phase C jsonl，默认自动找最近")
    parser.add_argument(
        "--output",
        type=Path,
        default=PIPELINE_DIR / "50条完整样本.md",
        help="输出 md 路径",
    )
    args = parser.parse_args()

    path_a = args.phase_a or find_latest("phase_a_contexts.jsonl")
    path_b = args.phase_b or find_latest("phase_b_multi_sku_blueprints.jsonl")
    path_c = args.phase_c or find_latest("phase_c_multi_sku_t2v_prompts.jsonl")
    if not (path_a and path_b and path_c):
        raise FileNotFoundError(
            f"找不到产物：A={path_a} B={path_b} C={path_c}"
        )
    print(f"[phase_a] {path_a}")
    print(f"[phase_b] {path_b}")
    print(f"[phase_c] {path_c}")

    phase_a = read_jsonl(path_a)
    phase_b = read_jsonl(path_b)
    phase_c = read_jsonl(path_c)
    md = build_md(phase_a, phase_b, phase_c)
    args.output.write_text(md, encoding="utf-8")
    print(f"[wrote] {args.output} ({len(md)} chars)")


if __name__ == "__main__":
    main()
