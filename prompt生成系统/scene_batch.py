#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
场景类视频批量 prompt 生成 & Seedance 提交脚本（数据源：docs/视频标签表.csv）。

核心场景：秃秃在自己家 → 需要喂场景参考图（REF_SCENE_FILES 4 张：
  bedroom / living_bedroom / game_dressing / entrance）。
"人类世界" 类事件不通过本脚本（保留给 collection_batch 之类）。

完全复用 collection_batch 的工具函数：
  - run_one: 生成 + 提交 Seedance + 写 preview md
  - _write_preview_md / _build_image_table / _extract_raw_from_md
  - 下载 / 监控相关
只在「事件构造 + 主题/场景约束文本」上不同。

使用方式：
    python scene_batch.py parse                   # 预览解析结果
    python scene_batch.py generate --limit 10     # dry-run 前 10 条
    python scene_batch.py submit --limit 10       # 生成 + 提交 Seedance
    python scene_batch.py submit --reuse-raw      # 用现成的 prompt 直接提交
    python scene_batch.py download                # 下载成品
"""

import argparse
import csv
import json
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "prompt生成系统"))

from tutu_core.seedance_client import (
    download_video,
    query_task,
)

# 直接复用 collection_batch 的工具
from collection_batch import (
    DEFAULT_OUTPUT_DIR as COLLECTION_OUTPUT_DIR,
    _build_image_table,
    _extract_raw_from_md,
    _write_preview_md,
    run_one,
)

logger = logging.getLogger("scene_batch")

CSV_PATH = ROOT / "docs" / "视频标签表.csv"
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "output"
DEFAULT_PROMPT_DIR = DEFAULT_OUTPUT_DIR / "scene_prompts"
DEFAULT_TASKS_FILE = DEFAULT_OUTPUT_DIR / "scene_tasks.json"
DEFAULT_VIDEO_DIR = DEFAULT_OUTPUT_DIR / "videos_scene"

DATE_STR = "2026-04-26"


# ============================================================
# 约束文本（同 collection_batch 风格，刻意避开 CATEGORY_KEYWORDS 关键词）
# ============================================================

# 4 张可选场景图，给 LLM 选最贴合事件的那张
SCENE_LOCK_CONSTRAINT = (
    "【场景锁定硬约束（必须执行，优先级高于上面 system 中的 4cm 微缩规则）】"
    "本事件发生在【秃秃自己家】——一个**为 4cm 蘑菇身材量身打造的童话室内空间**。"
    "秃秃仍然是 4cm 高（system 规则不变），但本场景里的家具/装饰也是迷你尺度："
    "圆床与秃秃身长相当、矮柜与它腰齐、唱片机像茶杯大小、风铃像它的指甲盖。"
    "**家具尺寸和秃秃 1:1 相称**——不要写'书架是它身高 N 倍''像山一样大''对它来说太大''高耸''巨大'等比较；"
    "也不要把家具描写成'巨型'。镜头里的家具应该看起来正好适合秃秃使用。"
    "墙面奶白/暖米色、木地板、家具皆为可爱卡通风格。"
    "你必须从下面 4 张家中场景参考图里选出最贴合事件的那一张作为构图与陈设依据，"
    "在 prompt 里通过显式占位符 `{scene:KEY}` 引用：\n"
    "  - `{scene:bedroom}`        蘑菇卧室（圆床+唱片机+蒲公英摆件+绿色矮柜，窗外蓝天）\n"
    "  - `{scene:living_bedroom}` 客厅+卧室（粉色沙发+橙色台灯+Monday 日历+郁金香+床角）\n"
    "  - `{scene:game_dressing}`  游戏室+衣帽间（开放衣架+四件小衣服+镜子+红色瓶盖座椅+草坪地毯+唱片机）\n"
    "  - `{scene:entrance}`       玄关（拱形木门+玄关矮柜+米色羊皮地毯+粉色沙发角）\n"
    "在场景描述段写："
    "\"场景：参考{scene:KEY}场景图——<用一两句话复述参考图的关键陈设>，"
    "秃秃在该空间中的某个具体位置/家具上活动。\"\n"
    "整段分镜里至少**显式引用 1 次** `{scene:KEY}`，"
    "并保证镜头里出现的家具/装饰必须是该场景图实际存在的元素，"
    "不要凭空加入参考图里没有的家具，**也不要出现现代人类电器**（手机/电脑/微波炉等），"
    "装饰元素只能用自然/童话风（浆果、树叶、花瓣、草绳、萤火苔藓、蘑菇灯、风铃等）。"
)

# 复用 collection 风格的物理 + 主题约束（已洁净，不污染 classify_event）
PHYSICS_CONSTRAINT = (
    "【主题锁定硬约束（最高优先级，违反视为生成失败）】"
    "※ 必须围绕上方'分镜蓝本'里明确写出的主角动作/物体展开。"
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


# ============================================================
# CSV 解析 & event 构造
# ============================================================

def parse_csv(scene_filter: str = "秃秃自己家") -> list[dict]:
    """读 csv，返回指定场景下的事件 list。"""
    rows = []
    with CSV_PATH.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("场景", "").strip() == scene_filter:
                rows.append(r)
    return rows


def _make_event_id(filename: str, idx: int) -> str:
    """从 csv 文件名里提取一个稳定且短的 id（去 .mp4 / 去无关前缀）。"""
    name = filename.replace(".mp4", "")
    # 去掉 "家中-" / "重新生成/" 等前缀
    name = re.sub(r"^(家中-|重新生成/|下载_)", "", name)
    name = re.sub(r"[^\w一-鿿]", "_", name)
    return f"H{idx:02d}_{name[:30]}"


def items_to_events(items: list[dict]) -> list[dict]:
    """把 csv 行转成 generate_event_content 需要的 event dict。"""
    events = []
    for i, r in enumerate(items, 1):
        eid = _make_event_id(r["文件名"], i)
        title = f"秃秃·家中{eid}·{r['事件名称']}"
        # 蓝本扩写：把 csv 各字段拼成一段连续描述
        content_parts = []
        if r.get("事件名称"):
            content_parts.append(r["事件名称"])
        if r.get("描述"):
            content_parts.append(r["描述"])
        meta = []
        if r.get("时间") and r["时间"] != "不限":
            meta.append(f"时间:{r['时间']}")
        if r.get("天气") and r["天气"] != "不限":
            meta.append(f"天气:{r['天气']}")
        if r.get("秃秃情绪"):
            meta.append(f"情绪线:{r['秃秃情绪']}")
        if r.get("标签"):
            meta.append(f"关键意象:{r['标签']}")
        meta_str = ("（" + "；".join(meta) + "）") if meta else ""
        blueprint = "；".join(content_parts) + meta_str

        summary = (
            f"【分镜蓝本，严格按此展开】{blueprint} "
            f"{SCENE_LOCK_CONSTRAINT} "
            f"{PHYSICS_CONSTRAINT}"
        )

        events.append({
            "id": eid,
            "kind": "scene",
            "time": "12:00",
            "title": title,
            "summary": summary,
            "triggered_by": "scene",
            "user_related": False,
            "payload_tag": f"scene_{eid}".lower(),
            # 强制 category="秃秃家中"，加载专门的家中类指引（generation.py
            # 的 _get_category_guidance 里有对应段，明确禁止微缩反差描写、要求 {scene:KEY}
            # 占位符、限制装饰元素为自然童话风等）。
            # 仍然 skip_example=True：examples-library.md 没有匹配的家中范例，不加载。
            "category": "秃秃家中",
            "skip_example": True,
        })
    return events


# ============================================================
# CLI 子命令
# ============================================================

def cmd_parse(args):  # noqa: ARG001
    del args
    items = parse_csv()
    print(f"秃秃自己家事件: {len(items)} 条")
    for i, r in enumerate(items, 1):
        eid = _make_event_id(r["文件名"], i)
        print(f"  [{i:02d}] {eid:35s} | {r['事件名称']}")


def _filter_events(limit: int) -> list[dict]:
    items = parse_csv()
    if limit and limit > 0:
        items = items[:limit]
    return items_to_events(items)


def _run_generate_or_submit(args, dry_run: bool):
    events = _filter_events(args.limit)
    if not events:
        print("没有事件，退出")
        return
    prompt_dir = Path(args.prompt_dir) if args.prompt_dir else DEFAULT_PROMPT_DIR
    tasks_file = Path(args.tasks) if args.tasks else DEFAULT_TASKS_FILE
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    workers = max(1, args.workers)
    total = len(events)
    reuse_raw = getattr(args, "reuse_raw", False) and not dry_run

    print(f"[开始] {'生成(dry-run)' if dry_run else ('reuse-raw 提交' if reuse_raw else '生成 + 提交')} "
          f"{total} 个事件，并发 workers={workers}")
    print(f"[输出] prompt 目录: {prompt_dir}")
    if not dry_run:
        print(f"[输出] tasks 文件: {tasks_file}")

    results: list[dict] = []
    done = 0
    t0 = time.time()
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
                r = {"id": evt["id"], "kind": evt["kind"], "title": evt["title"],
                     "task_id": None, "error": f"worker 异常: {e}"}
            results.append(r)
            done += 1
            status = "✅" if (dry_run and not r.get("error")) or r.get("task_id") else "❌"
            msg = r.get("task_id") or r.get("error") or "prompt 已生成"
            elapsed = time.time() - t0
            print(f"  [{done:02d}/{total:02d} +{elapsed:5.1f}s] {status} "
                  f"{r['id']} {r['title']} -> {msg}", flush=True)

    results.sort(key=lambda r: r["id"])
    if not dry_run:
        with tasks_file.open("w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        ok = sum(1 for r in results if r.get("task_id"))
        print(f"\n[完成] {ok}/{total} 提交成功，tasks.json 已写入 {tasks_file}")
    else:
        ok = sum(1 for r in results if not r.get("error"))
        print(f"\n[完成] {ok}/{total} prompt 生成成功，文件在 {prompt_dir}")


def cmd_generate(args):
    _run_generate_or_submit(args, dry_run=True)


def cmd_submit(args):
    _run_generate_or_submit(args, dry_run=False)


def cmd_retry(args):
    """按 id 重做：重新生成 prompt + 提交 Seedance，覆盖 scene_tasks.json 对应行。
    用法: scene_batch.py retry --ids H05 H06 --download --wait 360
    --ids 用 short id 前缀（H05/H06 等），会自动匹配 H05_xxx / H06_xxx 完整 id。
    """
    short_ids = [x.strip() for x in args.ids if x.strip()]
    if not short_ids:
        print("[错误] 必须指定 --ids，例如 H05 H06")
        sys.exit(1)
    all_events = items_to_events(parse_csv())
    events_by_id = {e["id"]: e for e in all_events}
    # short_id 前缀匹配
    matched_events = []
    for sid in short_ids:
        cands = [e for e in all_events if e["id"].startswith(sid + "_") or e["id"] == sid]
        if not cands:
            print(f"[错误] 未匹配到 id 前缀: {sid}")
            sys.exit(1)
        matched_events.extend(cands)
    events = matched_events

    # --content-override SHORT_ID=TEXT 改写蓝本（绕审查）
    overrides = {}
    for pair in (args.content_override or []):
        if "=" not in pair:
            print(f"[错误] --content-override 需要 ID=TEXT 格式，收到 {pair!r}")
            sys.exit(1)
        k, v = pair.split("=", 1)
        overrides[k.strip()] = v.strip()
    # --extra-constraint SHORT_ID=TEXT 追加约束
    extras = {}
    for pair in (args.extra_constraint or []):
        if "=" not in pair:
            print(f"[错误] --extra-constraint 需要 ID=TEXT 格式，收到 {pair!r}")
            sys.exit(1)
        k, v = pair.split("=", 1)
        extras[k.strip()] = v.strip()

    if overrides or extras:
        for evt in events:
            # short_id 前缀匹配（如 "H05" 匹配完整 id "H05_..."）
            short_id = next(
                (sid for sid in (overrides | extras)
                 if evt["id"] == sid or evt["id"].startswith(sid + "_")),
                None,
            )
            if short_id and short_id in overrides:
                new_content = overrides[short_id]
                evt["summary"] = re.sub(
                    r"【分镜蓝本，严格按此展开】[^【]*",
                    f"【分镜蓝本，严格按此展开】{new_content} ",
                    evt["summary"], count=1,
                )
                print(f"[override] {evt['id']} 蓝本改为: {new_content[:50]}...")
            if short_id and short_id in extras:
                evt["summary"] += f" 【该条特定要求】{extras[short_id]}"
                print(f"[extra-constraint] {evt['id']} 追加: {extras[short_id][:50]}...")

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
    reuse_raw = getattr(args, "reuse_raw", False) and not dry_run

    print(f"[重做] {'dry-run' if dry_run else ('reuse-raw 提交' if reuse_raw else '生成 + 提交')} "
          f"{total} 个事件")
    if not dry_run:
        print(f"[输出] tasks 文件: {tasks_file}（按 id 覆盖对应行）")

    new_results: list[dict] = []
    done = 0
    t0 = time.time()
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
                r = {"id": evt["id"], "kind": evt["kind"], "title": evt["title"],
                     "task_id": None, "error": f"worker 异常: {e}"}
            new_results.append(r)
            done += 1
            status = "✅" if (dry_run and not r.get("error")) or r.get("task_id") else "❌"
            msg = r.get("task_id") or r.get("error") or "prompt 已生成"
            elapsed = time.time() - t0
            print(f"  [{done:02d}/{total:02d} +{elapsed:5.1f}s] {status} "
                  f"{r['id']} {r['title']} -> {msg}", flush=True)

    if dry_run:
        print(f"\n[完成] dry-run，prompt 在 {prompt_dir}")
        return

    for r in new_results:
        by_id[r["id"]] = r
    merged = sorted(by_id.values(), key=lambda t: t["id"])
    with tasks_file.open("w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    ok = sum(1 for r in new_results if r.get("task_id"))
    print(f"\n[完成] {ok}/{total} 重新提交成功，tasks.json 已更新")

    if args.download:
        if args.wait > 0:
            print(f"[等待] {args.wait} 秒后开始下载")
            time.sleep(args.wait)
        # 仅下重做的
        ids_to_dl = [r["id"] for r in new_results if r.get("task_id")]
        dl_args = argparse.Namespace(
            tasks=str(tasks_file),
            output=args.output,
            workers=args.dl_workers,
            ids=ids_to_dl,
            force=True,
        )
        cmd_download(dl_args)


def cmd_download(args):
    tasks_file = Path(args.tasks) if args.tasks else DEFAULT_TASKS_FILE
    output_dir = Path(args.output) if args.output else DEFAULT_VIDEO_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    with tasks_file.open(encoding="utf-8") as f:
        tasks = json.load(f)
    tasks = [t for t in tasks if t.get("task_id")]
    if args.ids:
        # 支持 short id 前缀匹配（如 "H02" 匹配 "H02_在玄关等人回来"）
        ids_set = set(args.ids)
        tasks = [
            t for t in tasks
            if t["id"] in ids_set
            or any(t["id"].startswith(sid + "_") or t["id"] == sid for sid in ids_set)
        ]
    workers = max(1, args.workers)
    print(f"[下载] {len(tasks)} 个 task，并发 workers={workers}")

    def _one(t):
        try:
            info = query_task(t["task_id"])
        except Exception as e:
            return f"❌ {t['id']} {t['title']}: query 异常 {e}"
        if info.get("status") != "succeeded":
            return f"⏳ {t['id']} {t['title']}: {info.get('status')}"
        url = info.get("content", {}).get("video_url")
        if not url:
            return f"❌ {t['id']} {t['title']}: 无 video_url"
        safe = re.sub(r"[^\w一-鿿\-]", "_", t["title"])
        dest = output_dir / f"scene_{t['id']}_{safe}.mp4"
        if args.force and dest.exists():
            dest.unlink()
        ok, msg = download_video(url, dest)
        return f"{'✅' if ok else '❌'} {t['id']} {t['title']}: {msg}"

    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(_one, t) for t in tasks]):
            done += 1
            print(f"  [{done:02d}/{len(tasks):02d} +{time.time()-t0:5.1f}s] {fut.result()}",
                  flush=True)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("parse", help="解析 csv 并预览（不调 LLM）")
    sp.set_defaults(func=cmd_parse)

    for name, help_text, func in (
        ("generate", "生成 prompt 但不提交 Seedance（dry-run）", cmd_generate),
        ("submit",   "生成 prompt 并提交 Seedance",            cmd_submit),
    ):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--limit", type=int, default=0, help="限制处理条数（0=全部）")
        sp.add_argument("--workers", type=int, default=6, help="并发数")
        sp.add_argument("--prompt-dir", type=str, default=None)
        sp.add_argument("--tasks", type=str, default=None)
        sp.add_argument("--reuse-raw", action="store_true",
                        help="（仅 submit）跳过 LLM，用 prompt-dir 现有 md 提交")
        sp.set_defaults(func=func)

    sp = sub.add_parser("retry", help="按 id 重做（覆盖 scene_tasks.json 对应行）")
    sp.add_argument("--ids", nargs="+", required=True,
                    help="要重做的 short id（如 H05 H06），会前缀匹配完整 id")
    sp.add_argument("--workers", type=int, default=6)
    sp.add_argument("--prompt-dir", type=str, default=None)
    sp.add_argument("--tasks", type=str, default=None)
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--reuse-raw", action="store_true",
                    help="跳过 LLM 调用，用 prompt-dir 现有 md 的 LLM 原始输出重新提交")
    sp.add_argument("--content-override", nargs="+", default=None,
                    metavar="ID=TEXT",
                    help="覆盖单条蓝本内容（绕开敏感词审查），如 H05=\"秃秃在窗台...\"")
    sp.add_argument("--extra-constraint", nargs="+", default=None,
                    metavar="ID=TEXT",
                    help="对单条追加特定约束（不覆盖蓝本，追加到 summary 末尾）")
    sp.add_argument("--download", action="store_true")
    sp.add_argument("--wait", type=int, default=360)
    sp.add_argument("--output", type=str, default=None)
    sp.add_argument("--dl-workers", type=int, default=8)
    sp.set_defaults(func=cmd_retry)

    sp = sub.add_parser("download", help="下载成品视频")
    sp.add_argument("--tasks", type=str, default=None)
    sp.add_argument("--output", type=str, default=None)
    sp.add_argument("--workers", type=int, default=8)
    sp.add_argument("--ids", nargs="+", default=None)
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_download)

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
