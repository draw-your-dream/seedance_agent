"""把 Phase A / B / C 的产出对齐写进一个 md。

每条展示：
- Phase A：A1 context 字段
- Phase B：随机选中的 SKU、三张参考图路径、标题
- Phase C：最终 Seedance T2V prompt
"""

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
    ordered = sorted(phase_c, key=lambda r: int(r.get("index", 0)))

    lines: list[str] = []
    lines.append("# TUTU Multi-SKU 5s Pipeline — Phase A + B + C 对齐")
    lines.append("")
    lines.append(
        f"按 `context_id` 对齐展示 Phase A 的 A1 context、Phase B 的 blueprint 和 Phase C 的最终 Seedance T2V prompt。"
        f"共 {len(ordered)} 条样本。"
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    for record in ordered:
        cid = record["context_id"]
        a = by_id_a.get(cid, {})
        b = by_id_b.get(cid, {})
        title = record.get("title", cid)
        sku_index = record.get("sku_index", "?")
        index = int(record.get("index", 0))

        lines.append(f"## {index:02d}. {title}")
        lines.append("")
        lines.append(f"- context_id: `{cid}`")
        lines.append(f"- sku_index: `{sku_index}`")
        lines.append("")

        lines.append("### Phase A — A1 Context")
        lines.append("")
        lines.append(f"- **slot**: `{a.get('slot', '')}` ({a.get('slot_time_hint', '')})")
        lines.append(f"- **weather**: {a.get('weather', '')}")
        lines.append(f"- **season / solar_term**: {a.get('season', '')} / {a.get('solar_term', '')}")
        lines.append(f"- **daily**: {a.get('daily', '')}")
        lines.append(f"- **background**: {a.get('background', '')}")
        lines.append(f"- **lifestyle_theme**: {a.get('lifestyle_theme', '')}")
        lines.append(f"- **action_theme**: {a.get('action_theme', '')}")
        lines.append(f"- **mood_theme**: {a.get('mood_theme', '')}")
        lines.append("")

        lines.append("### Phase B — Blueprint（SKU + 参考图路径 + 标题）")
        lines.append("")
        lines.append(f"- **title**: {b.get('title', '')}")
        lines.append(f"- **sku_index**: `{b.get('sku_index', '')}`")
        lines.append(f"- **sku_image_path**: `{b.get('sku_image_path', '')}`")
        lines.append(f"- **hand_foot_image_path**: `{b.get('hand_foot_image_path', '')}`")
        lines.append(f"- **mouth_image_path**: `{b.get('mouth_image_path', '')}`")
        lines.append("")

        lines.append("### Phase C — 最终 Seedance T2V Prompt")
        lines.append("")
        prompt = record.get("seedance_t2v_prompt", "").strip() or "（生成失败）"
        lines.append(prompt)
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="把 Phase A context + Phase B blueprint + Phase C prompt 对齐成一个 md")
    parser.add_argument("--phase-a", type=Path, help="Phase A jsonl，默认自动找最新")
    parser.add_argument("--phase-b", type=Path, help="Phase B jsonl，默认自动找最新")
    parser.add_argument("--phase-c", type=Path, help="Phase C jsonl，默认自动找最新")
    parser.add_argument(
        "--output",
        type=Path,
        default=PIPELINE_DIR / "ABC对齐.md",
        help="输出 md 路径",
    )
    args = parser.parse_args()

    path_a = args.phase_a or find_latest("phase_a_contexts.jsonl")
    path_b = args.phase_b or find_latest("phase_b_multi_sku_blueprints.jsonl")
    path_c = args.phase_c or find_latest("phase_c_multi_sku_t2v_prompts.jsonl")
    if not (path_a and path_b and path_c):
        raise FileNotFoundError(f"找不到产物：A={path_a} B={path_b} C={path_c}")
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
