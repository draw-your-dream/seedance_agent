#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
收集品 / 日常再现动画 批量 prompt 生成 & Seedance 提交脚本。

数据源：docs/collection.md 的两张 markdown 表
  - 表 1：20 个收集品（A-01..A-14, S-01..S-06）
  - 表 2：10 条日常再现动画（CB-01..CB-10）

完全复用 tutu_core 里已有的 pipeline：
  - generate_event_content（生成 prompt + 质量校验）
  - load_reference_images_for_prompt（匹配表情图）
  - submit_task（占位符替换 + 图片声明注入 + Seedance 提交 + prompt 归档）
  - query_task / download_video（结果轮询 + 下载）

不修改 tutu_core 或 prompt 生成系统里任何现有文件。

使用方式：
    # 仅解析预览（不调 LLM，不提交）
    python collection_batch.py parse

    # 生成 prompt 但不提交 Seedance（dry run，写到 output/collection_prompts/）
    python collection_batch.py generate --dry-run

    # 生成 + 提交（默认），tasks.json 写到 output/collection_tasks.json
    python collection_batch.py submit

    # 只处理其中一组
    python collection_batch.py submit --only collection
    python collection_batch.py submit --only reproduce

    # 下载已完成的视频
    python collection_batch.py download --tasks output/collection_tasks.json \\
        --output output/videos_collection/
"""

import argparse
import json
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tutu_core.config import (
    REF_IMAGE, REF_HAND_CLOSEUP, REF_MOUTH_SIDE, REF_BACK, REF_FULL_BODY,
    REF_EXPRESSION_FILES,
    REF_SCENE_FILES,
)
from tutu_core.generation_router import generate_event_content
from tutu_core.seedance_client import (
    download_video,
    load_reference_images_for_prompt,
    match_expressions,
    match_scenes,
    query_task,
    submit_task,
)

# 图片编号 → (用途描述, 文件路径) —— 用于 preview 的"🖼 上传图片"表
_FIXED_IMAGE_TABLE = [
    ("图片1 主参考图",   REF_IMAGE),
    ("图片2 肢体末端",   REF_HAND_CLOSEUP),
    ("图片3 张嘴",       REF_MOUTH_SIDE),
    ("图片4 屁股",       REF_BACK),
    ("图片5 全身比例",   REF_FULL_BODY),
]

logger = logging.getLogger("collection_batch")

COLLECTION_MD = ROOT / "docs" / "collection.md"
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "output"
DEFAULT_PROMPT_DIR = DEFAULT_OUTPUT_DIR / "collection_prompts"
DEFAULT_TASKS_FILE = DEFAULT_OUTPUT_DIR / "collection_tasks.json"
DEFAULT_VIDEO_DIR = DEFAULT_OUTPUT_DIR / "videos_collection"

DATE_STR = "2026-04-21"

# 按 id 序号均匀铺开一天的时间（仅影响 prompt 里的光线描述）
# 早 8 点到晚 21 点之间，避免过暗/过早时段
DAY_START_HOUR = 8
DAY_END_HOUR = 21


# ============================================================
# Markdown 表解析
# ============================================================

def _split_row(line: str) -> list[str]:
    """把一行 markdown 表切成 cell 列表。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_separator(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    # 所有 cell 都是 --- 形式
    cells = _split_row(stripped)
    return all(re.fullmatch(r":?-{2,}:?", c) for c in cells) and len(cells) >= 2


def _extract_tables(md_text: str) -> list[list[list[str]]]:
    """从 markdown 文本中提取所有表格，每个表返回二维 list（首行是表头）。"""
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    prev_header: list[str] | None = None
    for line in md_text.splitlines():
        if "|" not in line:
            if current:
                tables.append(current)
                current = []
            prev_header = None
            continue
        if _is_separator(line):
            # 分隔符：前一行若是 header，则这是一个新表的开始
            if prev_header is not None and not current:
                current = [prev_header]
            continue
        cells = _split_row(line)
        if len(cells) < 2:
            if current:
                tables.append(current)
                current = []
            prev_header = None
            continue
        if current:
            current.append(cells)
        else:
            prev_header = cells
    if current:
        tables.append(current)
    return tables


def parse_collection_md(md_path: Path = COLLECTION_MD) -> tuple[list[dict], list[dict]]:
    """
    返回 (collection_items, reproduce_items)。
    每个 item 是 dict：
      collection: {id, level, name, icon, trigger, intro, content}
      reproduce:  {id, related, content}
    """
    text = md_path.read_text(encoding="utf-8")
    tables = _extract_tables(text)
    if len(tables) < 2:
        raise ValueError(f"collection.md 只解析到 {len(tables)} 张表，预期至少 2 张")

    # 找到有"收集品"/"内容描述"表头的那张作为收集品表；
    # 找到有"关联收集品"表头的作为再现动画表。
    collection_items: list[dict] = []
    reproduce_items: list[dict] = []
    for tbl in tables:
        header = [h.strip() for h in tbl[0]]
        joined = "|".join(header)
        if "收集品" in joined and "内容描述" in joined:
            for row in tbl[1:]:
                if len(row) < 7:
                    continue
                collection_items.append({
                    "id": row[0],
                    "level": row[1],
                    "name": row[2],
                    "icon": row[3],
                    "trigger": row[4],
                    "intro": row[5],
                    "content": row[6].rstrip("*").strip(),
                })
        elif "关联收集品" in joined:
            for row in tbl[1:]:
                if len(row) < 3:
                    continue
                reproduce_items.append({
                    "id": row[0],
                    "related": row[1],
                    "content": row[2].rstrip("*").strip(),
                })

    return collection_items, reproduce_items


# ============================================================
# Items → events
# ============================================================

# 物理常识 & 因果连续性硬约束：作为 summary 的尾块注入。
# 目的是防止 LLM 跳过烹饪/加工过程、让物体凭空出现或消失。
# （塞在 event.summary 里，不改 tutu_core 的公共 system_prompt）
# 注意：本约束文本会被并入 event.summary，而 generation.classify_event 会从 summary 扫
# CATEGORY_KEYWORDS 决定加载哪一类 few-shot。所以这段文本里**绝不能出现任何分类关键词**
# （否则约束本身会把事件错分到"美食吃播/户外/美食制作/日常生活"等，加载错的 few-shot
# 把 LLM 带偏）。已避开的字：吃/啃/舔/品尝/尝/做/制作/烹/煮/烤/蒸/炸/煎/切/裱花/冲泡/
# 公园/花/树/山/风/雨/雪/海/湖/江南/樱花/散步/晒太阳/秋千/水坑/草地/沙滩/星星/月亮/
# 野餐/蝴蝶/瓢虫/赖床/起床/洗澡/睡觉/打扫/发呆/躲/藏/第一次/认识/发现/好奇/研究/打量/
# 扮演/工作/店员/师/员/摊/卖/开店/打工
PHYSICS_CONSTRAINT = (
    "【主题锁定硬约束（最高优先级，违反视为生成失败）】"
    "※ 必须围绕上方'分镜蓝本'里明确写出的主角物体/场景展开。"
    "蓝本主角是什么，视频里就必须出现什么；不允许把它替换成蓝本之外的任何主角。"
    "※ 范例段落只用来参考写法密度与节奏，不决定视频主题；主题以分镜蓝本为唯一来源。"
    "若范例和蓝本不一致，全部以蓝本为准。"
    "※ 分镜蓝本里列出的每一个动词必须按顺序在时间码段里逐个展示，"
    "不得删改、不得跳过、不得替换成别的动作。"
    "【物理常识与因果性硬约束（必须全部满足）】"
    "1) 物体守恒：前一段出现的物体不得在后一段凭空消失，"
    "除非有明确的放下/塞入容器/离开画面动作；后段要用的物体必须先在画面里建立位置。"
    "2) 因果连续：任何状态变化都必须有显式的动作过程。"
    "状态从 A 变成 B 必须先展示能让 A→B 的物理过程，不允许跳过过程瞬时变化。"
    "3) 角色铁律：秃秃嘴巴张开里面是黑色小圆洞，无牙齿无舌头；"
    "不应出现'咔嚓'硬物咀嚼声；进食只能表达为'嘴巴贴近 + 腮帮子鼓动'。"
    "3.1) 严禁说话/心理语言旁白：秃秃不能说人话。"
    "禁止任何引导词+引号语句的写法（如'仿佛在说X'、'心里想X'、'意思是X'、"
    "'示意X'、'用眼神告诉X'），也禁止引号里出现人称代词（我/你/咱）+ 句末助词的台词式短句。"
    "情绪只能通过表情/肢体动作/拟声词（嘟、哼哧、duang、啪叽、啊呜 等）传达。"
    "正例: 「点头然后定格」 / 「腮帮子鼓鼓地望向镜头，发出一声满足的嘟～」。"
    "4) 尺寸守恒：同一物体在多个分镜里尺寸要一致，不得忽大忽小。"
    "5) 空间一致：多角色同框时，相对位置/朝向在分镜间要连续，不得瞬移。"
)


def _spread_time(idx: int, total: int) -> str:
    """把 idx/total 均匀映射到 08:00-21:00 之间的 HH:MM 字符串。"""
    if total <= 1:
        hour = (DAY_START_HOUR + DAY_END_HOUR) // 2
        return f"{hour:02d}:00"
    span = DAY_END_HOUR - DAY_START_HOUR
    minute_total = int(span * 60 * idx / (total - 1))
    h = DAY_START_HOUR + minute_total // 60
    m = minute_total % 60
    return f"{h:02d}:{m:02d}"


def items_to_events(items: list[dict], kind: str) -> list[dict]:
    """把解析结果转成 generate_event_content 需要的 event dict。"""
    events = []
    n = len(items)
    for i, item in enumerate(items):
        t = _spread_time(i, n)
        if kind == "collection":
            name = item["name"]
            title = f"秃秃·收集品{item['id']} {name}"
            content = item["content"]
            summary = f"【分镜蓝本，严格按此展开】{content} {PHYSICS_CONSTRAINT}"
            tag = f"coll_{item['id'].replace('-', '').lower()}"
        elif kind == "reproduce":
            related = item["related"]
            title = f"秃秃·日常再现{item['id']}（关联 {related}）"
            content = item["content"]
            summary = f"【分镜蓝本，严格按此展开】{content} {PHYSICS_CONSTRAINT}"
            tag = f"repro_{item['id'].replace('-', '').lower()}"
        else:
            raise ValueError(f"未知 kind: {kind}")

        events.append({
            "id": item["id"],
            "kind": kind,
            "time": t,
            "title": title,
            "summary": summary,
            "triggered_by": "collection",
            "user_related": False,
            "payload_tag": tag,
            # 强制 category，绕过 classify_event 的关键词扫描——蓝本里偶发的关键字
            # （如"三花猫"含"花"、"舔"在吃播词表）会把事件错引到不相关的 few-shot
            # 范例上，把 LLM 带偏。配合 skip_example=True 完全不注入 few-shot，
            # 由蓝本+主题锁定约束决定主题最稳。category 保留以驱动 quality_review
            # 按类校验的那部分逻辑。
            "category": "日常生活",
            # collection 场景不加载 few-shot 范例——分镜蓝本已经写得非常具体，
            # few-shot 反而会干扰 LLM 忠实按蓝本展开（之前 CB-05/A-11 被吃播
            # few-shot 带偏写成马卡龙/水果挞就是典型症状）。
            "skip_example": True,
        })
    return events


# ============================================================
# Pipeline 单事件执行
# ============================================================

def _prompt_filename(evt: dict) -> str:
    safe = re.sub(r"[^\w一-龥\-]", "_", evt["title"])
    return f"{evt['kind']}_{evt['id']}_{safe}.md"


def _build_image_table(
    matched: list[str],
    matched_scenes: list[str] | None = None,
) -> list[tuple[str, Path]]:
    """根据匹配到的 key 列表，构建完整的上传图片表（5 固定 + N 表情 + M 场景）。

    顺序与 seedance_client.load_reference_images_for_prompt 一致：
    固定 5 张 → 表情图（按 match_expressions 顺序）→ 场景图（按 match_scenes 顺序）
    """
    table = list(_FIXED_IMAGE_TABLE)
    for i, key in enumerate(matched):
        path = REF_EXPRESSION_FILES.get(key)
        if path and path.exists():
            table.append((f"图片{6 + i} {key} 表情", path))
    base = 6 + len(matched)
    for i, key in enumerate(matched_scenes or []):
        path = REF_SCENE_FILES.get(key)
        if path and path.exists():
            table.append((f"图片{base + i} {key} 场景", path))
    return table


def _write_preview_md(
    path: Path,
    evt: dict,
    content: dict,
    raw_prompt: str,
    final_prompt: str | None,
    matched: list[str],
    image_table: list[tuple[str, Path]],
    task_id: str | None,
) -> None:
    """按照 preview_v1plus 的排版规范把单条事件落盘。"""
    lines = [f"# [collection] {evt['title']}", ""]

    info_parts = [
        f"事件时间: {evt.get('time', '-')}",
        f"类别: {content.get('category', '-')}",
        f"匹配表情: {matched if matched else '[]'}",
    ]
    lines.append("- " + " | ".join(info_parts))
    lines.append(f"- kind: {evt['kind']} | id: {evt['id']} | payload_tag: `{evt['payload_tag']}`")
    if task_id:
        lines.append(f"- task_id: `{task_id}`")
    else:
        lines.append("- task_id: _(dry-run 未提交)_")
    raw_len = len(raw_prompt or "")
    final_len = len(final_prompt or "")
    lines.append(f"- 图片总数: {len(image_table)} | 字数: raw={raw_len}, final={final_len}")

    # 蓝本 & 该条可能的 override / extra constraint 都在 summary 里，一起存档
    lines += ["", "## 📜 summary（含蓝本+约束）", "", evt["summary"]]

    # 🧠 完整 LLM 输入（system_prompt + user_prompt）—— 100% 还原 LLM 看到的内容
    sys_p = content.get("_llm_system_prompt")
    usr_p = content.get("_llm_user_prompt")
    if sys_p or usr_p:
        lines += [
            "",
            "## 🧠 LLM 完整输入",
            "",
            "<details><summary>system_prompt（点击展开）</summary>",
            "",
            "```",
            (sys_p or "(未捕获)").rstrip(),
            "```",
            "",
            "</details>",
            "",
            "<details><summary>user_prompt（点击展开）</summary>",
            "",
            "```",
            (usr_p or "(未捕获)").rstrip(),
            "```",
            "",
            "</details>",
        ]

    # 上传图片表
    lines += [
        "",
        "## 🖼 上传图片",
        "",
        "| # | 用途 | 路径 |",
        "|---|------|------|",
    ]
    for idx, (desc, p) in enumerate(image_table, start=1):
        lines.append(f"| {idx} | {desc} | `{p}` |")

    # 最终 prompt（含注入的声明段）
    lines += [
        "",
        "## 📝 最终 prompt（Seedance 收到）",
        "",
        "```",
        final_prompt if final_prompt else "(dry-run 未提交，无最终 prompt)",
        "```",
    ]

    # LLM 原始输出（含 {placeholder}）
    lines += [
        "",
        "## 📄 LLM 原始输出（含 {{placeholder}}）",
        "",
        "```",
        raw_prompt,
        "```",
        "",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")


def _extract_raw_from_md(md_path: Path) -> str | None:
    """从 preview 格式的 .md 里提取"LLM 原始输出"段（含占位符）。"""
    if not md_path.exists():
        return None
    text = md_path.read_text(encoding="utf-8")
    m = re.search(r"## 📄 LLM 原始输出[^\n]*\n\n```\n(.+?)\n```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # 兼容旧格式
    m = re.search(r"## video_prompt\n\n(.+?)(?=\n## |\Z)", text, re.DOTALL)
    return m.group(1).strip() if m else None


def run_one(evt: dict, dry_run: bool, prompt_dir: Path, reuse_raw: bool = False) -> dict:
    """生成 prompt（+ 可选提交）。返回结果 dict。

    reuse_raw=True 时跳过 LLM 调用，直接从 prompt_dir 里的 .md 读之前生成的
    LLM 原始输出——用于"dry-run 后检查 → 用户确认 → 直接 submit"不再重跑 LLM 的场景。
    """
    result = {
        "id": evt["id"],
        "kind": evt["kind"],
        "title": evt["title"],
        "task_id": None,
        "error": None,
    }

    if reuse_raw:
        md_path = prompt_dir / _prompt_filename(evt)
        raw_prompt = _extract_raw_from_md(md_path)
        if not raw_prompt:
            result["error"] = f"reuse-raw 模式但 {md_path.name} 找不到 LLM 原始输出"
            return result
        # reuse 时 content 是 stub，只保留 category（从 event 或默认值）
        content = {"category": evt.get("category", "-"), "inner_voice": ""}
    else:
        try:
            content = generate_event_content(evt, DATE_STR)
        except Exception as e:
            result["error"] = f"generate_event_content 异常: {e}"
            return result
        if not content or not content.get("video_prompt"):
            result["error"] = "LLM 未产出 prompt"
            return result
        raw_prompt = content["video_prompt"]

    result["category"] = content.get("category")
    result["inner_voice"] = content.get("inner_voice")

    matched = match_expressions(raw_prompt)
    matched_scenes = match_scenes(raw_prompt)
    image_table = _build_image_table(matched, matched_scenes)
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = prompt_dir / _prompt_filename(evt)

    task_id = None
    final_prompt = None

    if not dry_run:
        # 提交 Seedance
        try:
            img_b64, _ = load_reference_images_for_prompt(raw_prompt)
            task_id, err = submit_task(raw_prompt, img_b64, payload_tag=evt["payload_tag"])
        except Exception as e:
            result["error"] = f"submit_task 异常: {e}"
        else:
            result["task_id"] = task_id
            if err:
                result["error"] = err
            # submit_task 提交成功后会把最终 prompt 归档到 submitted_prompts/{task_id}.txt
            # 读回来作为"Seedance 实际收到的文本"
            if task_id:
                archived = (
                    DEFAULT_OUTPUT_DIR / "submitted_prompts" / f"{task_id}.txt"
                )
                if archived.exists():
                    full = archived.read_text(encoding="utf-8")
                    # 头 3 行是 "# payload_tag / # task_id / # length" + 空行，去掉
                    parts = full.split("\n", 4)
                    final_prompt = parts[-1] if len(parts) >= 5 else full

    _write_preview_md(
        prompt_path,
        evt=evt,
        content=content,
        raw_prompt=raw_prompt,
        final_prompt=final_prompt,
        matched=matched,
        image_table=image_table,
        task_id=task_id,
    )
    result["prompt_file"] = str(prompt_path)
    return result


# ============================================================
# CLI 子命令
# ============================================================

def cmd_parse(args):  # noqa: ARG001 — argparse dispatch 要求统一签名
    del args
    collection, reproduce = parse_collection_md()
    print(f"收集品：{len(collection)} 条")
    for c in collection:
        print(f"  {c['id']} [{c['level']}] {c['name']} — {c['content'][:40]}")
    print(f"\n日常再现动画：{len(reproduce)} 条")
    for r in reproduce:
        print(f"  {r['id']} ({r['related']}) — {r['content'][:40]}")


def _filter_events(only: str | None) -> list[dict]:
    collection, reproduce = parse_collection_md()
    events = []
    if only in (None, "collection", "all"):
        events += items_to_events(collection, "collection")
    if only in (None, "reproduce", "all"):
        events += items_to_events(reproduce, "reproduce")
    return events


def cmd_generate(args):
    """生成 prompt（默认 dry-run=True）。"""
    _run_generate_or_submit(args, dry_run=True)


def cmd_submit(args):
    """生成 + 提交 Seedance。"""
    _run_generate_or_submit(args, dry_run=False)


def _run_generate_or_submit(args, dry_run: bool):
    events = _filter_events(args.only)
    if args.limit:
        events = events[:args.limit]
    if not events:
        print("没有可处理的事件，退出")
        return

    prompt_dir = Path(args.prompt_dir) if args.prompt_dir else DEFAULT_PROMPT_DIR
    tasks_file = Path(args.tasks) if args.tasks else DEFAULT_TASKS_FILE
    tasks_file.parent.mkdir(parents=True, exist_ok=True)

    workers = max(1, args.workers)
    total = len(events)
    print(f"[开始] {'生成(dry-run)' if dry_run else '生成 + 提交'} {total} 个事件，并发 workers={workers}")
    print(f"[输出] prompt 目录: {prompt_dir}")
    if not dry_run:
        print(f"[输出] tasks 文件: {tasks_file}")

    results: list[dict] = []
    done = 0
    t0 = time.time()
    reuse_raw = getattr(args, "reuse_raw", False)
    if reuse_raw and dry_run:
        print("[提示] dry-run + reuse-raw 没意义，自动忽略 reuse-raw")
        reuse_raw = False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(run_one, evt, dry_run, prompt_dir, reuse_raw): evt
            for evt in events
        }
        for fut in as_completed(futures):
            evt = futures[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {
                    "id": evt["id"], "kind": evt["kind"], "title": evt["title"],
                    "task_id": None, "error": f"worker 异常: {e}",
                }
            results.append(r)
            done += 1
            status = (
                "✅" if (dry_run and not r.get("error")) or r.get("task_id")
                else "❌"
            )
            msg = r.get("task_id") or r.get("error") or "prompt 已生成"
            elapsed = time.time() - t0
            print(
                f"  [{done:02d}/{total:02d} +{elapsed:5.1f}s] {status} "
                f"{r['id']} {r['title']} -> {msg}",
                flush=True,
            )

    # 按 id 排序输出，方便 diff
    results.sort(key=lambda r: (r["kind"], r["id"]))

    if not dry_run:
        with tasks_file.open("w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        ok = sum(1 for r in results if r.get("task_id"))
        print(f"\n[完成] {ok}/{len(results)} 提交成功，tasks.json 已写入 {tasks_file}")
    else:
        ok = sum(1 for r in results if not r.get("error"))
        print(f"\n[完成] {ok}/{len(results)} prompt 生成成功，文件在 {prompt_dir}")


def cmd_repreview(args):
    """从现有 tasks.json + submitted_prompts + 旧 collection_prompts 重组 preview
    格式的 .md，不调 LLM，不提交 Seedance。用于升级已有 md 的排版。"""
    tasks_file = Path(args.tasks) if args.tasks else DEFAULT_TASKS_FILE
    prompt_dir = Path(args.prompt_dir) if args.prompt_dir else DEFAULT_PROMPT_DIR
    with tasks_file.open(encoding="utf-8") as f:
        tasks = json.load(f)
    tasks_by_id = {t["id"]: t for t in tasks}

    events = _filter_events(None)
    events_by_id = {e["id"]: e for e in events}

    only_ids = set(args.ids) if args.ids else None
    updated = 0
    for cid, evt in events_by_id.items():
        if only_ids and cid not in only_ids:
            continue
        task = tasks_by_id.get(cid)
        if not task:
            continue
        # 从旧 .md 提取 raw_prompt（LLM 原始输出，含 {placeholder}）
        old_md = prompt_dir / _prompt_filename(evt)
        if not old_md.exists():
            print(f"  ⚠️ {cid} 旧 md 不存在，跳过（需要先跑 retry/submit 生成原始 prompt）")
            continue
        old_text = old_md.read_text(encoding="utf-8")
        # 兼容两种旧格式：旧版有 "## video_prompt" 段；新版 preview 有 "## 📄 LLM 原始输出"
        raw_match = re.search(
            r"## 📄 LLM 原始输出[^\n]*\n\n```\n(.+?)\n```",
            old_text, re.DOTALL,
        ) or re.search(r"## video_prompt\n\n(.+?)(?=\n## |\Z)", old_text, re.DOTALL)
        if not raw_match:
            print(f"  ⚠️ {cid} 旧 md 里找不到 LLM 原始输出，跳过")
            continue
        raw_prompt = raw_match.group(1).strip()

        # 从 submitted_prompts 读 final_prompt
        task_id = task.get("task_id")
        final_prompt = None
        if task_id:
            archived = DEFAULT_OUTPUT_DIR / "submitted_prompts" / f"{task_id}.txt"
            if archived.exists():
                full = archived.read_text(encoding="utf-8")
                parts = full.split("\n", 4)
                final_prompt = parts[-1] if len(parts) >= 5 else full

        matched = match_expressions(raw_prompt)
        matched_scenes = match_scenes(raw_prompt)
        image_table = _build_image_table(matched, matched_scenes)
        content_stub = {"category": task.get("category", "-")}
        _write_preview_md(
            old_md, evt=evt, content=content_stub,
            raw_prompt=raw_prompt, final_prompt=final_prompt,
            matched=matched, image_table=image_table, task_id=task_id,
        )
        updated += 1
        print(f"  ✅ {cid} 已重写: {old_md.name}")
    print(f"\n[完成] 重写 {updated} 条 preview md")


def cmd_download(args):
    tasks_file = Path(args.tasks)
    if not tasks_file.exists():
        print(f"[错误] tasks 文件不存在: {tasks_file}")
        sys.exit(1)
    dl_args = argparse.Namespace(
        tasks=str(tasks_file),
        output=args.output,
        workers=args.workers,
    )
    ids = getattr(args, "ids", None)
    force = getattr(args, "force", False)
    _download_subset(dl_args, ids_to_download=ids, force=force)


def cmd_retry(args):
    """按 id 重做：重新生成 prompt + 提交 Seedance，覆盖 tasks.json 里对应行。"""
    ids = [x.strip() for x in args.ids if x.strip()]
    if not ids:
        print("[错误] 必须指定 --ids，例如 A-02 CB-01 A-04")
        sys.exit(1)

    all_events = _filter_events(None)  # collection + reproduce 全量
    events_by_id = {e["id"]: e for e in all_events}
    missing = [i for i in ids if i not in events_by_id]
    if missing:
        print(f"[错误] 未知 id: {missing}，有效 id：{sorted(events_by_id)}")
        sys.exit(1)
    events = [events_by_id[i] for i in ids]

    # 允许针对单 id 覆盖分镜蓝本内容（绕开 Gemini 误审 / 修措辞）
    # 格式: --content-override A-12="秃秃拿起耳机仓的一个耳机，躲进..."
    overrides = {}
    for pair in (args.content_override or []):
        if "=" not in pair:
            print(f"[错误] --content-override 需要 ID=内容 的格式，收到 {pair!r}")
            sys.exit(1)
        k, v = pair.split("=", 1)
        overrides[k.strip()] = v.strip()
    if overrides:
        for evt in events:
            if evt["id"] in overrides:
                new_content = overrides[evt["id"]]
                # 把 summary 里"分镜蓝本"那段替换成新的 content
                evt["summary"] = re.sub(
                    r"【分镜蓝本，严格按此展开】[^【]*",
                    f"【分镜蓝本，严格按此展开】{new_content} ",
                    evt["summary"],
                    count=1,
                )
                print(f"[override] {evt['id']} 蓝本改为: {new_content[:50]}...")

    # --extra-constraint ID="..." 单条追加特定约束（不覆盖蓝本，追加在 summary 末尾）
    # 用于某 id 需要传达一个通用约束覆盖不到的特定要求（如"眼睛形态不能变"、
    # "屏幕里的画面不能是实拍"等精细控制）。支持一次传多个 ID。
    extras = {}
    for pair in (args.extra_constraint or []):
        if "=" not in pair:
            print(f"[错误] --extra-constraint 需要 ID=约束文本 的格式，收到 {pair!r}")
            sys.exit(1)
        k, v = pair.split("=", 1)
        extras[k.strip()] = v.strip()
    if extras:
        for evt in events:
            if evt["id"] in extras:
                extra = extras[evt["id"]]
                evt["summary"] = evt["summary"] + f" 【该条特定要求】{extra}"
                print(f"[extra-constraint] {evt['id']} 追加: {extra[:50]}...")

    tasks_file = Path(args.tasks) if args.tasks else DEFAULT_TASKS_FILE
    if not tasks_file.exists():
        print(f"[错误] tasks 文件不存在: {tasks_file}，请先跑 submit")
        sys.exit(1)
    with tasks_file.open(encoding="utf-8") as f:
        old_tasks = json.load(f)
    by_id = {t["id"]: t for t in old_tasks}

    prompt_dir = Path(args.prompt_dir) if args.prompt_dir else DEFAULT_PROMPT_DIR
    workers = max(1, args.workers)
    total = len(events)
    dry_run = args.dry_run

    print(f"[重做] {'dry-run' if dry_run else '生成 + 提交'} {total} 个事件: {ids}")
    print(f"[输出] prompt 目录: {prompt_dir}")
    if not dry_run:
        print(f"[输出] tasks 文件: {tasks_file}（将按 id 覆盖对应行）")

    new_results: list[dict] = []
    done = 0
    t0 = time.time()
    reuse_raw = getattr(args, "reuse_raw", False)
    if reuse_raw and dry_run:
        print("[提示] dry-run + reuse-raw 没意义，自动忽略 reuse-raw")
        reuse_raw = False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(run_one, evt, dry_run, prompt_dir, reuse_raw): evt
            for evt in events
        }
        for fut in as_completed(futures):
            evt = futures[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {
                    "id": evt["id"], "kind": evt["kind"], "title": evt["title"],
                    "task_id": None, "error": f"worker 异常: {e}",
                }
            new_results.append(r)
            done += 1
            status = "✅" if (dry_run and not r.get("error")) or r.get("task_id") else "❌"
            msg = r.get("task_id") or r.get("error") or "prompt 已生成"
            elapsed = time.time() - t0
            print(
                f"  [{done:02d}/{total:02d} +{elapsed:5.1f}s] {status} "
                f"{r['id']} {r['title']} -> {msg}",
                flush=True,
            )

    if dry_run:
        print(f"\n[完成] dry-run，prompt 在 {prompt_dir}")
        return

    # 覆盖 tasks.json：按 id 替换
    for r in new_results:
        by_id[r["id"]] = r
    merged = sorted(by_id.values(), key=lambda t: (t["kind"], t["id"]))
    with tasks_file.open("w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    ok = sum(1 for r in new_results if r.get("task_id"))
    print(f"\n[完成] {ok}/{total} 重新提交成功，tasks.json 已更新")

    if args.download:
        # 等 Seedance 渲染一点时间再下，避免全是 ⏳
        wait_s = args.wait
        if wait_s > 0:
            print(f"[等待] {wait_s} 秒后开始下载（等 Seedance 渲染）")
            time.sleep(wait_s)
        dl_args = argparse.Namespace(
            tasks=str(tasks_file),
            output=args.output,
            workers=args.dl_workers,
        )
        # retry 语义：重做意味着覆盖旧 mp4
        _download_subset(dl_args, ids_to_download=ids, force=True)


def _download_subset(args, ids_to_download: list[str] | None, force: bool = False):
    """download 的子集版本：只下 ids_to_download 里的。force=True 会先删旧文件再下。"""
    tasks_file = Path(args.tasks)
    with tasks_file.open(encoding="utf-8") as f:
        tasks = json.load(f)
    tasks = [t for t in tasks if t.get("task_id")]
    if ids_to_download is not None:
        ids_set = set(ids_to_download)
        tasks = [t for t in tasks if t["id"] in ids_set]
    output_dir = Path(args.output) if args.output else DEFAULT_VIDEO_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    workers = max(1, args.workers)
    total = len(tasks)
    print(f"[下载] {total} 个 task（子集），并发 workers={workers}{'，force 覆盖' if force else ''}")

    def _one(t):
        tid = t["task_id"]
        try:
            info = query_task(tid)
        except Exception as e:
            return f"❌ {t['id']} {t['title']}: query 异常 {e}"
        status = info.get("status")
        if status != "succeeded":
            return f"⏳ {t['id']} {t['title']}: {status}"
        url = info.get("content", {}).get("video_url")
        if not url:
            return f"❌ {t['id']} {t['title']}: 无 video_url"
        safe = re.sub(r"[^\w一-龥\-]", "_", t["title"])
        dest = output_dir / f"{t['kind']}_{t['id']}_{safe}.mp4"
        if force and dest.exists():
            dest.unlink()
        ok, msg = download_video(url, dest)
        return f"{'✅' if ok else '❌'} {t['id']} {t['title']}: {msg}"

    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(_one, t) for t in tasks]):
            done += 1
            elapsed = time.time() - t0
            print(f"  [{done:02d}/{total:02d} +{elapsed:5.1f}s] {fut.result()}", flush=True)


# ============================================================
# 入口
# ============================================================

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("parse", help="解析 collection.md 并预览（不调 LLM）")
    sp.set_defaults(func=cmd_parse)

    for name, help_text, func in (
        ("generate", "生成 prompt 但不提交 Seedance（dry-run）", cmd_generate),
        ("submit", "生成 prompt 并提交 Seedance", cmd_submit),
    ):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--only", choices=["collection", "reproduce", "all"], default=None,
                        help="只处理其中一组，默认两组都跑")
        sp.add_argument("--limit", type=int, default=0,
                        help="限制处理条数（调试用）")
        sp.add_argument("--workers", type=int, default=6,
                        help="并发 worker 数；每个 worker 独立跑 LLM 生成 → Seedance 提交 整条流程（默认 6）")
        sp.add_argument("--prompt-dir", type=str, default=None, help="prompt 输出目录")
        sp.add_argument("--tasks", type=str, default=None, help="tasks.json 路径")
        sp.add_argument("--dry-run", action="store_true",
                        help="（仅 submit 用）只生成 prompt 不提交")
        sp.add_argument("--reuse-raw", action="store_true",
                        help="（仅 submit 用）跳过 LLM 调用，直接用 prompt_dir 里已有 md 的 "
                             "'LLM 原始输出' 段提交 Seedance——用于 dry-run 检查后直接提交，"
                             "不重新调 LLM")
        sp.set_defaults(func=func)

    sp = sub.add_parser("repreview", help="用现有 tasks.json + submitted_prompts 重组 preview 格式 md（不调 LLM）")
    sp.add_argument("--tasks", type=str, default=None)
    sp.add_argument("--prompt-dir", type=str, default=None)
    sp.add_argument("--ids", nargs="+", default=None, help="只处理指定 id（默认全部）")
    sp.set_defaults(func=cmd_repreview)

    sp = sub.add_parser("download", help="根据 tasks.json 批量下载成品视频")
    sp.add_argument("--tasks", type=str, default=str(DEFAULT_TASKS_FILE))
    sp.add_argument("--output", type=str, default=None)
    sp.add_argument("--workers", type=int, default=8, help="下载并发数（默认 8）")
    sp.add_argument("--ids", nargs="+", default=None, help="只下指定 id（默认下全部）")
    sp.add_argument("--force", action="store_true", help="已有 mp4 强制覆盖")
    sp.set_defaults(func=cmd_download)

    sp = sub.add_parser("retry", help="按 id 重做（重新生成 + 提交，覆盖 tasks.json 对应行）")
    sp.add_argument("--ids", nargs="+", required=True,
                    help="要重做的事件 id，空格分隔，如 A-02 CB-01 A-04")
    sp.add_argument("--workers", type=int, default=6, help="生成并发（默认 6）")
    sp.add_argument("--prompt-dir", type=str, default=None)
    sp.add_argument("--tasks", type=str, default=None)
    sp.add_argument("--dry-run", action="store_true", help="只生成 prompt 不提交")
    sp.add_argument("--reuse-raw", action="store_true",
                    help="跳过 LLM 调用，直接用现有 md 里的 'LLM 原始输出' 段提交")
    sp.add_argument("--download", action="store_true", help="提交完成后自动下载重做那几条")
    sp.add_argument("--wait", type=int, default=360,
                    help="提交后等多少秒再下载（Seedance 渲染耗时，默认 360 秒）")
    sp.add_argument("--output", type=str, default=None, help="下载输出目录")
    sp.add_argument("--dl-workers", type=int, default=8, help="下载并发（默认 8）")
    sp.add_argument("--content-override", nargs="+", default=None,
                    metavar="ID=TEXT",
                    help="覆盖单条蓝本内容（绕开敏感词审查），如 A-12=\"秃秃躲进耳机仓...\"")
    sp.add_argument("--extra-constraint", nargs="+", default=None,
                    metavar="ID=TEXT",
                    help="对单条追加特定约束（不覆盖蓝本，追加到 summary 末尾），"
                         "如 A-14=\"秃秃的眼睛形态必须始终保持圆形...\"")
    sp.set_defaults(func=cmd_retry)

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return

    # generate 子命令强制 dry-run
    if args.cmd == "generate":
        args.dry_run = True
    args.func(args)


if __name__ == "__main__":
    main()
