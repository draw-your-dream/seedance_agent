#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于 4 张 scene 参考图自动探索式生成 50 条秃秃家中视频。

流程（全程不调用人工，不修改现有文件）：

  Stage 1: Gemini Vision 看每张参考图 → 生成元素清单（具体到秃秃可互动颗粒度）
  Stage 2: 文本 LLM 基于每张图的元素清单 → 生成 12/13 个秃秃活动事件
           （含跨 scene 去重：后面 scene 看见前面 scene 已用的事件名，避开）
           合计 50 条，4 张图分配 13/13/12/12
  Stage 3: 把 50 条事件 → event dict（强制 {scene:KEY} 占位符锁参考图）
           复用 collection_batch.run_one 走完整 LLM 生成 prompt + Seedance 提交流程
  Stage 4: 等 Seedance 渲染 → 下载 mp4

只读现有 PIPELINE 框架，不修改任何现有文件。

中间产物缓存：
  output/scene_explore/analyses.json     # 4 张图的元素清单
  output/scene_explore/events.json       # 50 条事件清单
  output/scene_explore/explore_tasks.json   # task_id 清单
  output/scene_explore/prompts/*.md      # preview 格式 md
  output/videos_explore/*.mp4            # 成品视频

用法：
  python scene_explore.py analyze        # Stage 1（缓存）
  python scene_explore.py plan           # Stage 2（缓存）
  python scene_explore.py preview        # 看前几条 Stage 2 结果
  python scene_explore.py submit         # Stage 3（生成 prompt + Seedance 提交）
  python scene_explore.py download       # Stage 4
  python scene_explore.py auto           # 一键跑全 4 阶段（跳过缓存已有的）
"""

import argparse
import base64
import json
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "prompt生成系统"))

from tutu_core.config import (
    REF_SCENE_FILES,
    get_gemini_api_key,
    GEMINI_URL,
)
from tutu_core.llm_client import call_llm, extract_json
from tutu_core.seedance_client import (
    download_video,
    query_task,
)
# 复用 collection_batch 的工具函数（run_one / 下载等）
from collection_batch import run_one  # noqa

logger = logging.getLogger("scene_explore")

OUTPUT_DIR = Path(__file__).parent / "output" / "scene_explore"
ANALYSES_FILE = OUTPUT_DIR / "analyses.json"
EVENTS_FILE = OUTPUT_DIR / "events.json"
TASKS_FILE = OUTPUT_DIR / "explore_tasks.json"
PROMPT_DIR = OUTPUT_DIR / "prompts"
VIDEO_DIR = Path(__file__).parent / "output" / "videos_explore"

DATE_STR = "2026-04-28"
TARGET_TOTAL = 50
SCENE_KEYS = ["bedroom", "living_bedroom", "game_dressing", "entrance"]
QUOTAS = [13, 13, 12, 12]  # 13+13+12+12 = 50

# Gemini Vision 默认 URL（内置在 GEMINI_URL 是 generateContent，相同接口接图像 inline_data）
_http_client = httpx.Client(timeout=120, follow_redirects=True)

# 复用 scene_batch 的强约束（避免循环 import，直接 inline 一份）
SCENE_LOCK_CONSTRAINT = (
    "【场景锁定硬约束（必须执行，优先级高于上面 system 中的 4cm 微缩规则）】"
    "本事件发生在【秃秃自己家】——一个**为 4cm 蘑菇身材量身打造的童话室内空间**。"
    "秃秃仍然是 4cm 高（system 规则不变），但本场景里的家具/装饰也是迷你尺度："
    "圆床与秃秃身长相当、矮柜与它腰齐、唱片机像茶杯大小、风铃像它的指甲盖。"
    "**家具尺寸和秃秃 1:1 相称**——不要写'书架是它身高 N 倍''像山一样大''对它来说太大''高耸''巨大'等比较；"
    "也不要把家具描写成'巨型'。镜头里的家具应该看起来正好适合秃秃使用。"
    "墙面奶白/暖米色、木地板、家具皆为可爱卡通风格。"
)
PHYSICS_CONSTRAINT = (
    "【主题锁定硬约束（最高优先级，违反视为生成失败）】"
    "※ 必须围绕上方'分镜蓝本'里明确写出的主角动作/物体展开。"
    "※ 范例段落只用来参考写法密度与节奏，不决定视频主题。"
    "※ 分镜蓝本里列出的每一个动词必须按顺序在时间码段里逐个展示。"
    "【物理常识与因果性硬约束（必须全部满足）】"
    "1) 物体守恒；2) 因果连续；3) 角色铁律：秃秃嘴里黑色小圆洞，无牙齿无舌头；"
    "3.1) 严禁说话/心理语言旁白：秃秃不能说人话。"
    "禁止任何引导词+引号语句的写法（如'仿佛在说X'、'心里想X'、'意思是X'、"
    "'示意X'、'用眼神告诉X'），也禁止引号里出现人称代词（我/你/咱）+ 句末助词的台词式短句。"
    "情绪只能通过表情/肢体动作/拟声词（嘟、哼哧、duang、啪叽、啊呜 等）传达。"
    "4) 尺寸守恒；5) 空间一致。"
)


# ============================================================
# Stage 1: Gemini Vision 看图
# ============================================================

def _gemini_vision(image_path: Path, prompt_text: str) -> str | None:
    """直接 POST Gemini API，带图片 inline_data。返回 text 或 None。"""
    api_key = get_gemini_api_key()
    image_b64 = base64.b64encode(image_path.read_bytes()).decode()
    payload = {
        "contents": [{
            "role": "user",
            "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                {"text": prompt_text},
            ],
        }],
    }
    try:
        resp = _http_client.post(
            GEMINI_URL,
            headers={"Content-Type": "application/json", "X-goog-api-key": api_key},
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        logger.error(f"Gemini Vision 调用失败 ({image_path.name}): {e}")
        return None


def analyze_scene(scene_key: str) -> dict | None:
    image_path = REF_SCENE_FILES[scene_key]
    prompt = """你正在帮一只 4cm 高的小蘑菇角色（叫秃秃）规划在自家房间里能做的有趣活动。

请仔细看这张参考图，输出 JSON（只输出 JSON，不要别的）：
{
  "atmosphere": "整体氛围一句话（暖色童话/治愈安静/活泼缤纷 等）",
  "light": "光线特征（窗外日光/橘色台灯/萤火青光 等）",
  "color": "色彩基调（米色+橘色+绿色 等主色）",
  "elements": [
    "家具/装饰名 - 简短特征",
    "...每个 1 行，列 25-35 个，覆盖：家具、墙面装饰、地面摆件、植物、光影、颜色、纹理等",
    "每个元素要具体到秃秃能与之做出有趣互动的颗粒度（如：'椭圆形粉色沙发，靠背有小三角折痕'）"
  ]
}

要求：
- elements 必须严格基于这张图实际看到的内容，不要编造
- 不要描写画面里的人物（图里没人）
- elements 数量 25-35 之间
"""
    raw = _gemini_vision(image_path, prompt)
    if not raw:
        return None
    try:
        data = extract_json(raw)
        if isinstance(data.get("elements"), list) and data["elements"]:
            return data
    except (json.JSONDecodeError, ValueError, KeyError):
        pass
    logger.warning(f"{scene_key} JSON 解析失败，原文前 300 字: {raw[:300]}")
    return None


def cmd_analyze(args):
    del args
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if ANALYSES_FILE.exists():
        analyses = json.loads(ANALYSES_FILE.read_text(encoding="utf-8"))
        print(f"[skip] {ANALYSES_FILE} 已存在，4 张图分析:")
        for k in SCENE_KEYS:
            if k in analyses:
                print(f"  ✅ {k}: {len(analyses[k]['elements'])} 个元素")
            else:
                print(f"  ❌ {k}: 缺")
        return analyses

    print(f"[Stage1] 调 Gemini Vision 分析 {len(SCENE_KEYS)} 张参考图...")
    analyses = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(analyze_scene, k): k for k in SCENE_KEYS}
        for fut in as_completed(futures):
            k = futures[fut]
            d = fut.result()
            if d:
                analyses[k] = d
                print(f"  ✅ {k}: 元素 {len(d['elements'])} 个 / {d['atmosphere'][:30]}")
            else:
                print(f"  ❌ {k}: 失败")

    ANALYSES_FILE.write_text(json.dumps(analyses, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[完成] 写入 {ANALYSES_FILE}")
    return analyses


# ============================================================
# Stage 2: LLM 基于元素清单生成事件
# ============================================================

def generate_events_for_scene(
    scene_key: str,
    analysis: dict,
    count: int,
    exclude_names: list[str],
) -> list[dict]:
    """返回 list of {"name": "...", "blueprint": "...", "emotion": "..."}"""
    sys_prompt = """你是给 4cm 高小蘑菇秃秃设计独居家中视频活动的创意编剧。

要求：
- 秃秃在自己家里独处，没有人类在画面里
- 家具尺度与秃秃匹配（家不是人类世界，是为 4cm 蘑菇打造的童话空间）
- 装饰元素只能是自然/童话风：浆果、树叶、花瓣、草绳、萤火苔藓、蘑菇灯、唱片、风铃等；禁止现代人类电器
- 节奏温馨慢，主打"独处也很满足"
- 每个活动**主要互动对象必须来自参考图给出的元素清单**（让视频和参考图视觉锚定）
- 严禁角色说话/写台词/写心理独白
- 情绪从 5 类里选：开心/大笑/委屈哭泣/害羞/生气奶凶
"""

    excl = "、".join(exclude_names) if exclude_names else "（无）"
    user_prompt = f"""场景：{scene_key}
氛围：{analysis['atmosphere']}
光线：{analysis['light']}
色彩：{analysis['color']}

可识别元素清单（请从这里挑互动对象）：
{chr(10).join('- ' + e for e in analysis['elements'])}

已用事件名（必须避开，主题不能与之雷同）：
{excl}

请生成 {count} 个不重复的秃秃家中视频活动想法。每个想法必须：
- name: 活动名（4-10 字简洁动词短语，如"挂花瓣风铃"、"擦唱片机"、"给抱枕讲故事"）
- blueprint: 分镜蓝本（25-50 字，按时间顺序写 4 个动作节拍：发生XX → 做XX → 出现小波折/小惊喜 → 满足收尾）
- emotion: 情绪线（用→连接 2-3 个情绪词，从 5 类里选）
- main_object: 主要互动对象（从元素清单里精确挑一项）

严格 JSON 输出（只输出 JSON 数组）：
[
  {{
    "name": "...",
    "blueprint": "...",
    "emotion": "好奇→满足",
    "main_object": "..."
  }},
  ...共 {count} 项
]
"""
    raw = call_llm(sys_prompt, user_prompt, max_tokens=4000, use_cache=False)
    if not raw:
        return []
    try:
        # 兼容 ```json ... ``` 包裹
        m = re.search(r'\[\s*\{.*\}\s*\]', raw, re.DOTALL)
        if not m:
            return []
        events = json.loads(m.group())
        if not isinstance(events, list):
            return []
        return events[:count]
    except json.JSONDecodeError:
        return []


def cmd_plan(args):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    append = getattr(args, "append", False)

    existing_events: list[dict] = []
    if EVENTS_FILE.exists():
        existing_events = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
        if not append:
            print(f"[skip] {EVENTS_FILE} 已存在，{len(existing_events)} 条事件（用 --append 追加新一批）")
            return existing_events

    if not ANALYSES_FILE.exists():
        print("[错误] 请先跑 analyze")
        sys.exit(1)
    analyses = json.loads(ANALYSES_FILE.read_text(encoding="utf-8"))

    if append:
        print(f"[Stage2-append] 已有 {len(existing_events)} 条，追加生成 {TARGET_TOTAL} 条不重复事件...")
    else:
        print(f"[Stage2] 基于元素清单生成 {TARGET_TOTAL} 个不重复事件...")

    all_events: list[dict] = []
    # 已有事件名（包括跨批次）作为 exclude 起点
    used_names: list[str] = [e["name"] for e in existing_events if e.get("name")]
    for k, q in zip(SCENE_KEYS, QUOTAS):
        if k not in analyses:
            print(f"  ⚠️ {k} 缺分析数据，跳过")
            continue
        # 多尝试一次（如果第一次产出不足）
        produced = generate_events_for_scene(k, analyses[k], q, used_names)
        # 去掉与已用名重复的
        seen = set(used_names)
        filtered = [e for e in produced if e.get("name") and e["name"] not in seen]
        # 不够再补一次
        if len(filtered) < q:
            extra = generate_events_for_scene(
                k, analyses[k], q - len(filtered),
                used_names + [e["name"] for e in filtered],
            )
            filtered += [e for e in extra if e.get("name") and e["name"] not in seen]
        filtered = filtered[:q]
        for e in filtered:
            e["scene"] = k
            used_names.append(e["name"])
            all_events.append(e)
        print(f"  ✅ {k}: 产出 {len(filtered)}/{q} 条")

    # append 模式：追加在已有事件后面（id offset 自然继承）
    final_events = existing_events + all_events if append else all_events
    EVENTS_FILE.write_text(
        json.dumps(final_events, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"[完成] 本轮新增 {len(all_events)} 条，"
          f"events.json 共 {len(final_events)} 条，写入 {EVENTS_FILE}")
    return final_events


def cmd_preview(args):
    del args
    if not EVENTS_FILE.exists():
        print("请先跑 plan")
        return
    events = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
    print(f"共 {len(events)} 条事件:\n")
    by_scene: dict[str, list] = {}
    for e in events:
        by_scene.setdefault(e["scene"], []).append(e)
    for k in SCENE_KEYS:
        items = by_scene.get(k, [])
        print(f"=== {k} ({len(items)} 条) ===")
        for i, e in enumerate(items, 1):
            print(f"  {i:02d}. {e['name']:20s} [{e.get('emotion', '?')}] | {e['blueprint'][:60]}")
        print()


# ============================================================
# Stage 3: 转 event dict + 提交
# ============================================================

def _make_eid(idx: int, scene: str, name: str) -> str:
    safe = re.sub(r"[^\w一-鿿]", "", name)[:14]
    return f"E{idx:02d}_{scene[:3]}_{safe}"


def to_pipeline_events(planned: list[dict]) -> list[dict]:
    out = []
    for i, e in enumerate(planned, 1):
        scene = e["scene"]
        eid = _make_eid(i, scene, e["name"])
        title = f"秃秃·探索E{i:02d}·{e['name']}"
        meta = []
        if e.get("emotion"):
            meta.append(f"情绪线:{e['emotion']}")
        if e.get("main_object"):
            meta.append(f"主要互动对象:{e['main_object']}")
        meta_str = ("（" + "；".join(meta) + "）") if meta else ""
        # 强制 LLM 在 prompt 里使用 {scene:KEY} 占位符
        scene_directive = (
            f"【场景图绑定（不可更换）】本事件**必须**使用 `{{scene:{scene}}}` 场景参考图。"
            f"在场景描述段写：'场景：参考{{scene:{scene}}}场景图——<复述参考图陈设>，"
            f"秃秃在 <main_object> 上活动'。整段分镜里至少**显式引用 1 次** `{{scene:{scene}}}`。"
        )
        summary = (
            f"【分镜蓝本，严格按此展开】{e['name']}；{e['blueprint']}{meta_str} "
            f"{scene_directive} "
            f"{SCENE_LOCK_CONSTRAINT} "
            f"{PHYSICS_CONSTRAINT}"
        )
        out.append({
            "id": eid,
            "kind": "explore",
            "time": "12:00",
            "title": title,
            "summary": summary,
            "triggered_by": "scene_explore",
            "user_related": False,
            "payload_tag": f"expl_{eid}".lower(),
            "category": "秃秃家中",
            "skip_example": True,
            # 备注信息，仅供 .md 留痕
            "_scene": scene,
            "_planned_name": e["name"],
            "_main_object": e.get("main_object"),
            "_emotion": e.get("emotion"),
        })
    return out


def cmd_submit(args):
    if not EVENTS_FILE.exists():
        print("请先跑 plan")
        sys.exit(1)
    planned = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
    events = to_pipeline_events(planned)
    PROMPT_DIR.mkdir(parents=True, exist_ok=True)

    # 已有 task 跳过（增量提交）
    existing = {}
    if TASKS_FILE.exists():
        existing = {t["id"]: t for t in json.loads(TASKS_FILE.read_text(encoding="utf-8"))}
    todo = [e for e in events if not existing.get(e["id"], {}).get("task_id")]
    print(f"[Stage3] 共 {len(events)} 条，已提交 {len(events) - len(todo)} 条，待提交 {len(todo)} 条")
    if not todo:
        return

    workers = max(1, args.workers)
    results: list[dict] = list(existing.values())
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_one, evt, False, PROMPT_DIR): evt for evt in todo}
        for fut in as_completed(futures):
            evt = futures[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {"id": evt["id"], "kind": evt["kind"], "title": evt["title"],
                     "task_id": None, "error": f"worker 异常: {e}"}
            # 覆盖旧记录
            results = [x for x in results if x["id"] != r["id"]]
            results.append(r)
            done += 1
            status = "✅" if r.get("task_id") else "❌"
            msg = r.get("task_id") or r.get("error") or "?"
            print(f"  [{done:02d}/{len(todo):02d} +{time.time()-t0:5.1f}s] {status} {r['id']} -> {msg}",
                  flush=True)

    results.sort(key=lambda r: r["id"])
    TASKS_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in results if r.get("task_id"))
    print(f"\n[完成] {ok}/{len(events)} 提交成功，tasks 写入 {TASKS_FILE}")


def cmd_download(args):
    if not TASKS_FILE.exists():
        print("请先跑 submit")
        sys.exit(1)
    tasks = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    tasks = [t for t in tasks if t.get("task_id")]
    if args.ids:
        ids_set = set(args.ids)
        tasks = [t for t in tasks
                 if t["id"] in ids_set or any(t["id"].startswith(s + "_") for s in ids_set)]
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    workers = max(1, args.workers)

    def _one(t):
        try:
            info = query_task(t["task_id"])
        except Exception as e:
            return f"❌ {t['id']}: query 异常 {e}"
        if info.get("status") != "succeeded":
            return f"⏳ {t['id']}: {info.get('status')}"
        url = info.get("content", {}).get("video_url")
        if not url:
            return f"❌ {t['id']}: 无 video_url"
        safe = re.sub(r"[^\w一-鿿\-]", "_", t["title"])
        dest = VIDEO_DIR / f"{t['id']}_{safe}.mp4"
        if args.force and dest.exists():
            dest.unlink()
        ok, msg = download_video(url, dest)
        return f"{'✅' if ok else '❌'} {t['id']}: {msg}"

    print(f"[下载] {len(tasks)} 个 task")
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(_one, t) for t in tasks]):
            done += 1
            print(f"  [{done:02d}/{len(tasks):02d}] {fut.result()}", flush=True)


def cmd_auto(args):
    """一键跑 4 阶段（缓存已有的跳过 LLM）。"""
    cmd_analyze(argparse.Namespace())
    cmd_plan(argparse.Namespace())
    cmd_submit(args)
    if args.wait > 0:
        print(f"[等待] {args.wait} 秒后开始下载（Seedance 渲染）")
        time.sleep(args.wait)
    dl_args = argparse.Namespace(workers=args.workers, force=True, ids=None)
    cmd_download(dl_args)


def main():
    logging.basicConfig(level=logging.INFO,
                         format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("analyze", help="Stage 1: Gemini Vision 分析 4 张参考图").set_defaults(func=cmd_analyze)
    sp = sub.add_parser("plan", help="Stage 2: LLM 生成 50 个不重复事件")
    sp.add_argument("--append", action="store_true",
                    help="追加生成新一批（不覆盖现有 events.json，新事件接在末尾，跨批次去重）")
    sp.set_defaults(func=cmd_plan)
    sub.add_parser("preview", help="预览 plan 结果").set_defaults(func=cmd_preview)

    sp = sub.add_parser("submit", help="Stage 3: LLM 写 prompt + Seedance 提交")
    sp.add_argument("--workers", type=int, default=8)
    sp.set_defaults(func=cmd_submit)

    sp = sub.add_parser("download", help="Stage 4: 下载成品")
    sp.add_argument("--workers", type=int, default=8)
    sp.add_argument("--ids", nargs="+", default=None)
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_download)

    sp = sub.add_parser("auto", help="一键 analyze→plan→submit→等→download")
    sp.add_argument("--workers", type=int, default=8)
    sp.add_argument("--wait", type=int, default=420, help="提交后等几秒再下载（默认 420）")
    sp.set_defaults(func=cmd_auto)

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
