"""把 Phase A + B + C + D 的产出打包到最终归档目录。

每条产出对应两个文件：
- NNNN.txt：Phase A context + Phase B blueprint + Phase C prompt + Phase D task 状态
- NNNN.mp4：下载好的视频

NNNN 从 0000 开始，**追加模式**：自动检测目标目录里已有的最大编号，新批次从下一个编号开始命名。

典型用法：
    python package_to_final.py \
        --phase-c-jsonl outputs/multi_sku_t2v_prompts/phase_c_batch90.jsonl \
        --output-dir outputs/2500多sku
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any


PIPELINE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PIPELINE_DIR / "outputs"

DEFAULT_PHASE_A_GLOB = OUTPUT_DIR / "phase_a"
DEFAULT_PHASE_B = OUTPUT_DIR / "multi_sku_blueprints" / "phase_b_multi_sku_blueprints.jsonl"
DEFAULT_TASKS = OUTPUT_DIR / "multi_sku_t2v_videos" / "multi_sku_t2v_tasks.jsonl"
DEFAULT_PACKAGE_DIR = OUTPUT_DIR / "2500多sku"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def find_phase_a_for_contexts(context_ids: set[str]) -> dict[str, dict[str, Any]]:
    """在 outputs/phase_a/*/phase_a_contexts.jsonl 里反查这些 context_id。

    优先扫最近修改的，命中即收集。允许跨多个 run。
    """
    result: dict[str, dict[str, Any]] = {}
    if not DEFAULT_PHASE_A_GLOB.exists():
        return result
    candidates = sorted(
        [p for p in DEFAULT_PHASE_A_GLOB.glob("*/phase_a_contexts.jsonl") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        for rec in read_jsonl(path):
            cid = rec.get("context_id")
            if cid in context_ids and cid not in result:
                result[cid] = rec
        if len(result) >= len(context_ids):
            break
    return result


def next_start_index(package_dir: Path) -> int:
    """检测 package_dir 里已有的 NNNN.txt / NNNN.mp4，返回下一个起始编号。"""
    if not package_dir.exists():
        return 0
    pattern = re.compile(r"^(\d{4})\.(?:txt|mp4)$")
    indices = []
    for p in package_dir.iterdir():
        m = pattern.match(p.name)
        if m:
            indices.append(int(m.group(1)))
    return max(indices) + 1 if indices else 0


def build_txt(name: str, c_rec: dict[str, Any], a: dict[str, Any], b: dict[str, Any], t: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# {name} — {c_rec.get('title', '')}")
    lines.append("")
    lines.append(f"context_id: {c_rec.get('context_id', '')}")
    lines.append(f"sku_index: {b.get('sku_index', '')}  ({b.get('sku_name', '')})")
    lines.append("")
    lines.append("## Phase A — A1 Context")
    lines.append("")
    lines.append(f"- slot: {a.get('slot', '')} ({a.get('slot_time_hint', '')})")
    lines.append(f"- weather: {a.get('weather', '')}")
    lines.append(f"- season / solar_term: {a.get('season', '')} / {a.get('solar_term', '')}")
    lines.append(f"- daily: {a.get('daily', '')}")
    lines.append(f"- background: {a.get('background', '')}")
    lines.append(f"- lifestyle_theme: {a.get('lifestyle_theme', '')}")
    lines.append(f"- action_theme: {a.get('action_theme', '')}")
    lines.append(f"- mood_theme: {a.get('mood_theme', '')}")
    lines.append("")
    lines.append("## Phase B — Blueprint")
    lines.append("")
    lines.append(f"- title: {b.get('title', '')}")
    lines.append(f"- sku_index: {b.get('sku_index', '')}")
    lines.append(f"- sku_name: {b.get('sku_name', '')}")
    lines.append(f"- sku_full_phrase: {b.get('sku_full_phrase', '')}")
    lines.append(f"- sku_image_path: {b.get('sku_image_path', '')}")
    lines.append(f"- hand_foot_image_path: {b.get('hand_foot_image_path', '')}")
    lines.append(f"- mouth_image_path: {b.get('mouth_image_path', '')}")
    lines.append(f"- butt_image_path: {b.get('butt_image_path', '')}")
    lines.append("")
    lines.append("## Phase C — Seedance T2V Prompt")
    lines.append("")
    lines.append(c_rec.get("seedance_t2v_prompt", "").strip())
    lines.append("")
    lines.append("## Phase D — Task")
    lines.append("")
    lines.append(f"- task_id: {t.get('task_id', '')}")
    lines.append(f"- status: {t.get('status', '')}")
    lines.append(f"- video_url: {t.get('video_url', '')}")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    pkg_dir: Path = args.output_dir
    pkg_dir.mkdir(parents=True, exist_ok=True)

    phase_c_records = read_jsonl(args.phase_c_jsonl)
    phase_c_records.sort(key=lambda r: int(r.get("index", 0)))
    if args.limit:
        phase_c_records = phase_c_records[: args.limit]
    if not phase_c_records:
        raise ValueError(f"Empty phase_c jsonl: {args.phase_c_jsonl}")

    phase_b_index = {r["context_id"]: r for r in read_jsonl(args.phase_b_jsonl)}
    tasks_index = {r["context_id"]: r for r in read_jsonl(args.tasks_jsonl)}
    context_ids = {r["context_id"] for r in phase_c_records}
    phase_a_index = find_phase_a_for_contexts(context_ids)

    start = next_start_index(pkg_dir) if args.start_index is None else args.start_index
    print(f"[package] dir={pkg_dir} start_index={start:04d} count={len(phase_c_records)}")

    written = 0
    skipped_no_video = 0
    for i, c_rec in enumerate(phase_c_records):
        idx = start + i
        name = f"{idx:04d}"
        cid = c_rec["context_id"]
        a = phase_a_index.get(cid, {})
        b = phase_b_index.get(cid, {})
        t = tasks_index.get(cid, {})

        (pkg_dir / f"{name}.txt").write_text(build_txt(name, c_rec, a, b, t), encoding="utf-8")

        src_video = t.get("download_path")
        if src_video and Path(src_video).exists():
            shutil.copy2(src_video, pkg_dir / f"{name}.mp4")
            written += 1
        else:
            skipped_no_video += 1
            print(f"  [warn] {name} no video (status={t.get('status', '?')})")

    return {
        "package_dir": str(pkg_dir),
        "start_index": start,
        "count": len(phase_c_records),
        "videos_copied": written,
        "skipped_no_video": skipped_no_video,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把 A+B+C+D 产出打包到最终归档目录")
    parser.add_argument("--phase-c-jsonl", type=Path, required=True, help="本批 Phase C prompts 的 jsonl 路径")
    parser.add_argument("--phase-b-jsonl", type=Path, default=DEFAULT_PHASE_B, help="Phase B blueprint jsonl 路径")
    parser.add_argument("--tasks-jsonl", type=Path, default=DEFAULT_TASKS, help="Phase D tasks jsonl 路径")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_PACKAGE_DIR, help="归档目录")
    parser.add_argument("--start-index", type=int, default=None, help="起始编号（默认自动检测目录里已有的最大编号 +1）")
    parser.add_argument("--limit", type=int, default=0, help="只打包前 N 条，0=全部")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
