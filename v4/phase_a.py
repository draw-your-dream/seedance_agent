from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from collections import Counter
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

GEMINI_API_KEY = os.environ.get("PICAA_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
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

    # 严格 JSON 失败时尝试找完整的 [...] 或 {...}
    for pattern in (r"\[\s*{.*}\s*\]", r"\{.*\}"):
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                continue

    # 截断恢复：响应被切断时（比如流被 Lambda 30s 限制截断），
    # 扫描所有顶层完整对象 {...}，组成数组返回
    partial = _recover_partial_array(text)
    if partial:
        return partial
    return default


def _recover_partial_array(text: str) -> list[dict[str, Any]]:
    """从（可能被截断的）JSON 数组文本里提取所有顶层完整对象。"""
    # 先找数组起点 [
    bracket_idx = text.find("[")
    if bracket_idx < 0:
        return []
    objects: list[dict[str, Any]] = []
    depth = 0
    obj_start: int | None = None
    in_str = False
    escape = False
    i = bracket_idx + 1
    while i < len(text):
        ch = text[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\":
            escape = True
            i += 1
            continue
        if ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "{":
                if depth == 0:
                    obj_start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and obj_start is not None:
                    obj_str = text[obj_start:i + 1]
                    try:
                        objects.append(json.loads(obj_str))
                    except json.JSONDecodeError:
                        pass
                    obj_start = None
        i += 1
    return objects


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


# === Claude via Picaa Bedrock 网关 ===
CLAUDE_API_BASE = os.environ.get("CLAUDE_API_BASE", "https://api.picaa.ai")
CLAUDE_MODEL_ID = os.environ.get("CLAUDE_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
CLAUDE_JWT_TOKEN = os.environ.get("CLAUDE_JWT_TOKEN", "")


def _parse_eventstream(data: bytes) -> list[dict[str, Any]]:
    """解析 AWS Bedrock converse-stream 返回的二进制 EventStream，抽出每帧 JSON payload。"""
    events: list[dict[str, Any]] = []
    offset = 0
    while offset + 12 <= len(data):
        total_len = int.from_bytes(data[offset:offset + 4], "big")
        headers_len = int.from_bytes(data[offset + 4:offset + 8], "big")
        if total_len == 0 or offset + total_len > len(data):
            break
        payload_start = offset + 12 + headers_len
        payload_end = offset + total_len - 4  # 减去尾部 message CRC
        payload = data[payload_start:payload_end]
        try:
            events.append(json.loads(payload.decode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        offset += total_len
    return events


def call_claude(system_prompt: str, user_prompt: str, timeout: int = 600) -> str:
    """通过 Picaa 网关调 Claude (Bedrock converse-stream API)。

    用 streaming 才能突破 API Gateway 29s 超时限制。
    需要环境变量 CLAUDE_JWT_TOKEN。其它两个 CLAUDE_API_BASE / CLAUDE_MODEL_ID 有默认值。

    用 http.client 而不是 urllib，因为 urllib 会把 header 名 .capitalize()，
    而 Lambda 网关对 'aws-endpoint-prefix' 头大小写敏感（需要全小写）。
    """
    import http.client
    from urllib.parse import urlparse

    if not CLAUDE_JWT_TOKEN:
        raise RuntimeError("缺少环境变量 CLAUDE_JWT_TOKEN（Picaa Bedrock 网关 JWT）")

    full_url = f"{CLAUDE_API_BASE}/model/{CLAUDE_MODEL_ID}/converse-stream"
    parsed = urlparse(full_url)
    payload = {
        "system": [{"text": system_prompt}],
        "messages": [{"role": "user", "content": [{"text": user_prompt}]}],
        "inferenceConfig": {"temperature": 0.9, "maxTokens": 1500},
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    conn = http.client.HTTPSConnection(parsed.netloc, timeout=timeout)
    try:
        conn.putrequest("POST", parsed.path or "/", skip_host=False, skip_accept_encoding=False)
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Authorization", f"Bearer {CLAUDE_JWT_TOKEN}")
        conn.putheader("aws-endpoint-prefix", "bedrock-runtime")
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        conn.send(body)
        resp = conn.getresponse()
        # 容错：流可能被 Lambda 提前关闭，IncompleteRead 时用拿到的部分继续解析
        try:
            resp_body = resp.read()
        except http.client.IncompleteRead as exc:
            resp_body = exc.partial
            print(f"[claude-stream] IncompleteRead, 用部分数据 {len(resp_body)} bytes", flush=True)
        if resp.status != 200:
            raise RuntimeError(f"Claude HTTP {resp.status}: {resp_body[:500].decode('utf-8', errors='replace')}")
    finally:
        conn.close()

    events = _parse_eventstream(resp_body)
    text_parts: list[str] = []
    for ev in events:
        delta = ev.get("delta", {})
        if isinstance(delta, dict) and "text" in delta:
            text_parts.append(delta["text"])
    if not text_parts:
        raise RuntimeError(
            "Claude stream 没拿到任何 text delta；前 300 字节："
            + resp_body[:300].decode("utf-8", errors="replace")
        )
    return "".join(text_parts)


# 默认 LLM 路由：CLAUDE_JWT_TOKEN 设了就走 Claude；否则走 Gemini。
def call_llm(system_prompt: str, user_prompt: str, timeout: int = 240) -> str:
    if CLAUDE_JWT_TOKEN:
        return call_claude(system_prompt, user_prompt, timeout=timeout)
    return call_gemini(system_prompt, user_prompt, timeout=timeout)


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


# A 类预分配场景池（涵盖真实生活/工作 + 大量娱乐/艺术/游玩/打卡场景）
A_SCENE_POOL = [
    # 日常生活/工作
    "早市菜场买菜的小摊", "咖啡馆吧台", "便利店冷柜前", "邮局柜台", "药店货架",
    "洗衣店折衣台", "修鞋摊矮凳", "理发店镜子前", "花店橱窗", "宠物店玻璃缸边",
    "牙科诊所候诊椅", "健身房哑铃架", "游泳馆出发台", "攀岩馆抱石区", "瑜伽馆地垫",
    "舞蹈室把杆", "书店童书区", "图书馆借阅台", "面包店烤箱前", "蛋糕店裱花台",
    "甜品店玻璃柜", "茶馆茶台", "酒馆吧台", "夜市烤串摊", "民宿庭院吊椅",
    "自行车工坊修车架", "汽车修理厂引擎盖前", "苗圃育苗床", "蜂场蜂箱旁", "奶牛场挤奶位",
    # 文化/艺术/演艺/展览（去玩去逛）
    "美术馆现代艺术展厅", "博物馆恐龙骨架展区", "博物馆古埃及展厅", "博物馆青铜器展柜前",
    "画廊抽象油画展品前", "雕塑展户外大型装置旁", "陶瓷艺术展玻璃柜前", "摄影展黑白人像墙前",
    "剧院舞台幕后", "歌剧院观众席最前排", "音乐厅大提琴演奏侧台", "音乐节草地观众区",
    "演唱会摇滚区", "电影院售票口", "电影院 IMAX 大厅", "话剧院后台道具间",
    "街头乐队卖艺现场", "广场音乐喷泉旁", "灯光秀打卡点", "夜空烟花表演海滩",
    "庙会糖画摊位", "灯会龙凤灯笼下", "嘉年华小丑表演", "漫展手办展位",
    "游园会古风舞台", "艺术装置打卡墙", "太空展火箭模型前", "VR 体验馆头盔区",
    # 娱乐/游艺
    "游乐园旋转木马", "游乐园过山车排队队伍", "游乐园摩天轮缆车里", "游乐园海盗船甲板",
    "游乐园碰碰车场地", "蹦床馆弹床中央", "密室逃脱解谜房间", "KTV 包厢沙发",
    "游艺厅抓娃娃机", "游艺厅赛车机", "桌游吧麻将台", "桌游吧狼人杀桌",
    "保龄球馆球道前", "台球馆击球区", "斯诺克球台旁", "射箭馆靶位",
    "卡丁车赛道发车区",
    # 自然/旅游/户外
    "动物园企鹅馆", "动物园熊猫馆", "动物园长颈鹿喂食区", "动物园海狮表演池",
    "水族馆鲨鱼隧道", "海洋馆海豚表演池", "水族馆水母展缸",
    "植物园温室热带角", "植物园樱花林步道", "植物园玫瑰园", "植物园荷花池小桥",
    "公园长椅", "广场喷泉边", "果园苹果树下", "葡萄园采收筐边", "稻田田埂",
    "海边沙堡", "海边贝壳沙滩", "海边日落观景礁石", "山顶观景台", "山间溪流木桥",
    "瀑布观景栈道", "湖泊木码头", "河滩鹅卵石滩", "峡谷吊桥栏杆", "雪山滑雪道",
    "温泉露天池边", "滑雪场缆车站",
    # 交通/旅途
    "机场登机口", "机场观景台看飞机", "火车站售票窗口", "高铁站候车区",
    "港口栈桥", "地铁站台边", "公交车站", "缆车厢内", "热气球吊篮",
    "帆船甲板", "邮轮游泳池边", "邮轮甲板观海", "观鲸船船尾", "观光巴士顶层敞篷",
    # 特殊场景（户外探索 / 冷门）
    "考古挖掘探方", "潜水基地装备架", "冲浪海滩冲浪板边", "滑翔伞起跳台",
    "灯塔顶层观景窗", "天文台望远镜旁", "天文台观星露台", "宠物咖啡馆撸猫沙发",
    "鸟语林观鸟台", "蝴蝶馆植物丛", "萤火虫观赏夜林步道", "瀑布溶洞内",
    "向日葵花田中央", "薰衣草田间小径", "稻草人节南瓜田",
    "街头涂鸦墙下", "滑板公园 U 池边",
]

# B 类预分配 IP 角色池（60+ 经典角色，跨 5 大文化圈）
B_IP_POOL = [
    # 欧美电影动漫
    ("欧美", "哈利·波特", "霍格沃茨学院袍 + 圆框眼镜 + 闪电疤 + 魔杖", "霍格沃茨魔法学校大厅"),
    ("欧美", "钢铁侠", "红金色机甲 + 胸口反应堆", "复仇者大厦实验室"),
    ("欧美", "蜘蛛侠", "红蓝紧身服 + 蜘蛛纹", "纽约高楼天台"),
    ("欧美", "蝙蝠侠", "黑色斗篷 + 蝙蝠耳朵 + 腰带", "哥谭市屋顶"),
    ("欧美", "爱莎（冰雪奇缘）", "蓝色冰雪长裙 + 雪花披肩", "冰雪城堡台阶"),
    ("欧美", "灰姑娘", "蓝色舞会礼服 + 水晶鞋", "舞会台阶"),
    ("欧美", "海绵宝宝", "方形白衬衫 + 红领带 + 方帽子", "比奇堡菠萝屋"),
    ("欧美", "米老鼠", "红短裤 + 黄色大鞋 + 圆耳朵", "迪士尼城堡前"),
    ("欧美", "汤姆和杰瑞的杰瑞", "灰色小老鼠 + 黄色奶酪", "厨房地板"),
    ("欧美", "小黄人", "黄色身体 + 蓝色背带裤 + 护目镜", "格鲁大叔实验室"),
    # 日漫日剧
    ("日漫", "哆啦A梦", "蓝色身体 + 红色项圈 + 四次元口袋", "野比家客厅"),
    ("日漫", "皮卡丘", "黄色身体 + 红色脸颊 + 闪电尾巴", "宝可梦草地"),
    ("日漫", "龙猫", "灰色大肚子 + 大眼睛 + 大耳朵", "森林橡树洞"),
    ("日漫", "千寻", "粉色衣服 + 短发 + 红发带", "汤屋走廊"),
    ("日漫", "孙悟空（七龙珠）", "橘色武道服 + 金色尖发", "龟仙人岛"),
    ("日漫", "路飞", "草帽 + 红色背心 + 蓝短裤", "黄金梅利号甲板"),
    ("日漫", "鸣人", "橘色忍者服 + 头戴木叶忍者头巾", "木叶村屋顶"),
    ("日漫", "蜡笔小新", "红色衬衫 + 黄短裤 + 鼻涕泡", "野原家客厅"),
    ("日漫", "樱桃小丸子", "粉色连衣裙 + 短发", "小学教室"),
    ("日漫", "迪迦奥特曼", "银红色光之巨人战斗服", "怪兽出没的城市顶楼"),
    # 国漫国剧
    ("国漫", "孙悟空（西游记）", "虎皮裙 + 紫金冠 + 金箍棒", "花果山水帘洞"),
    ("国漫", "葫芦娃大娃", "红色葫芦发饰 + 红色肚兜", "山顶葫芦藤旁"),
    ("国漫", "黑猫警长", "黑色警官制服 + 警帽", "森林派出所"),
    ("国漫", "喜羊羊", "白色绒毛 + 红色铃铛", "羊村青草地"),
    ("国漫", "灰太狼", "灰色毛 + 头盔 + 平底锅", "狼堡厨房"),
    ("国漫", "熊大", "棕色大熊 + 红色背带裤", "森林木屋前"),
    ("国漫", "光头强", "绿色伐木工服 + 黄色安全帽", "森林伐木场"),
    ("国漫", "舒克", "驾驶员盔帽 + 飞机", "玩具房机场跑道"),
    ("国漫", "济公", "破草帽 + 蒲扇 + 葫芦酒", "古庙台阶"),
    ("国漫", "白蛇传小青", "青色襦裙 + 长剑", "西湖断桥边"),
    # 游戏
    ("游戏", "超级马里奥", "红色M字帽 + 蓝色背带裤 + 红色按钮", "蘑菇王国砖块上"),
    ("游戏", "林克（塞尔达）", "绿色精灵帽 + 米色衬衫 + 大师剑", "海拉尔草原神庙旁"),
    ("游戏", "索尼克", "蓝色刺猬 + 红色运动鞋", "绿色山丘环形跑道"),
    ("游戏", "吃豆人", "黄色圆球 + 大嘴巴", "迷宫格子上"),
    ("游戏", "愤怒的小鸟红", "红色身体 + 黑眉毛", "弹弓阵地"),
    ("游戏", "豌豆射手（植物大战僵尸）", "绿色豌豆头 + 花盆", "草坪前院"),
    ("游戏", "星之卡比", "粉色圆球 + 红色脚 + 大眼睛", "梦幻泉水边"),
    ("游戏", "派蒙（原神）", "白色精灵 + 旅行小披风", "蒙德城风景"),
    ("游戏", "刺客信条刺客", "白色兜帽斗篷 + 隐刃", "中世纪屋顶"),
    ("游戏", "李白（王者荣耀）", "白色侠客长袍 + 长剑 + 酒葫芦", "竹林石板台阶"),
    # 童话经典
    ("童话", "爱丽丝（梦游仙境）", "天蓝色泡泡裙 + 白色围裙 + 黑色发带", "下午茶桌边"),
    ("童话", "绿野仙踪桃乐丝", "蓝白格子裙 + 红色魔法鞋", "黄砖路上"),
    ("童话", "彼得潘", "绿色尖帽 + 绿色羽毛", "永无岛海盗船桅杆"),
    ("童话", "小红帽", "红色斗篷 + 编花篮", "森林小路"),
    ("童话", "三只小猪盖砖房", "粉色小猪 + 砖块工具", "森林空地"),
    ("童话", "白雪公主", "蓝黄红裙 + 红色发带", "森林小屋台阶"),
    ("童话", "灰太狼对面的小红帽", "红色斗篷 + 编花篮", "林间小径"),
    ("童话", "丁丁（丁丁历险记）", "棕色短裤 + 蓝色毛衣 + 黄色卷发", "记者社办公桌"),
    ("童话", "小王子", "金色头发 + 蓝色斗篷 + 玫瑰", "B612小行星"),
    ("童话", "福尔摩斯", "猎鹿帽 + 烟斗 + 放大镜", "贝克街221B客厅"),
]


def simulate_fused_context_blueprints(run_label: str, count: int, seed: int) -> list[dict[str, Any]]:
    """生成蓝图。每条预分配 mode_hint (A 70% / B 30%) + 预分配场景或角色。

    A 类: 从 A_SCENE_POOL 不放回随机抽 (count 不超 pool 大小时绝不重复)
    B 类: 从 B_IP_POOL 按文化圈轮流抽 (确保 5 大文化圈尽可能均衡，且不重复)
    """
    rng = random.Random(seed)
    n_b = max(1, int(round(count * 0.2)))
    n_a = count - n_b

    # A 类场景：不放回抽 n_a 个
    a_scenes = list(A_SCENE_POOL)
    rng.shuffle(a_scenes)
    a_scenes = a_scenes[:n_a]
    if n_a > len(A_SCENE_POOL):
        # 不够就循环（极少 50 条以内不会发生）
        a_scenes = (a_scenes * ((n_a // len(A_SCENE_POOL)) + 1))[:n_a]

    # B 类角色：按文化圈分组，轮流抽，确保跨圈均衡
    from itertools import groupby
    b_pool_by_circle: dict[str, list] = {}
    for item in B_IP_POOL:
        b_pool_by_circle.setdefault(item[0], []).append(item)
    for circle in b_pool_by_circle:
        rng.shuffle(b_pool_by_circle[circle])
    circles_cycle = list(b_pool_by_circle.keys())
    rng.shuffle(circles_cycle)
    b_chars = []
    pool_idx = {c: 0 for c in b_pool_by_circle}
    while len(b_chars) < n_b:
        for c in circles_cycle:
            if len(b_chars) >= n_b:
                break
            if pool_idx[c] < len(b_pool_by_circle[c]):
                b_chars.append(b_pool_by_circle[c][pool_idx[c]])
                pool_idx[c] += 1

    # 构造 mode 序列并打乱位置
    mode_assignments = ["B"] * n_b + ["A"] * n_a
    rng.shuffle(mode_assignments)

    a_iter = iter(a_scenes)
    b_iter = iter(b_chars)

    contexts: list[dict[str, Any]] = []
    for idx in range(count):
        slot_info = TIME_SLOTS[idx % len(TIME_SLOTS)]
        weather = SIMULATED_WEATHERS[idx % len(SIMULATED_WEATHERS)]
        reference_hints = build_reference_catalog(slot_info["slot"], weather["weather"])
        mode = mode_assignments[idx]

        scene_assignment = ""
        ip_assignment = ""
        if mode == "A":
            scene_assignment = next(a_iter)
        else:
            circle, name, look, location = next(b_iter)
            ip_assignment = f"{name}（{circle}文化圈）— 标志服饰/特征：{look}；经典场景：{location}"

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
                "mode_hint": mode,
                "assigned_scene": scene_assignment,  # A 类预分配场景
                "assigned_ip": ip_assignment,         # B 类预分配 IP 角色
            }
        )
    return contexts


# 高频词统计用的简单中文 tokenizer：抽常见 1-2 字模式词
HIGH_FREQ_WHITELIST = re.compile(r"[一-龥]{2,4}")


def compute_top_freq_words(themes: list[str], top_n: int = 25) -> list[tuple[str, int]]:
    """从历史 action_theme 里粗略统计高频 2-4 字词（动词/物件/场景大类）。

    不维护词表；只是用 jieba 风格的滑窗截 2-4 字片段，再筛掉太短/太长/纯标点的。
    返回 [(word, count), ...] 按频次降序。
    """
    if not themes:
        return []
    counter: Counter[str] = Counter()
    # 只统计 2-3 字片段，避免噪音
    for theme in themes:
        # 去掉所有标点和空白
        clean = re.sub(r"[^一-龥]+", " ", theme)
        for chunk in clean.split():
            for n in (2, 3):
                for i in range(len(chunk) - n + 1):
                    word = chunk[i : i + n]
                    counter[word] += 1
    # 过滤"句式骨架"和"非主题描述词"——保留真正能引导 Gemini 选别主题的词
    # 主题词如"真正的""模仿""跑步""园艺"故意保留（这些是 Gemini 应该绕开的）
    stopwords = {
        # 主语 / 自身指代
        "秃秃", "秃正", "秃秃正", "蘑菇", "TUTU", "tutu", "蘑菇帽",
        "它两", "它两只", "它正",
        # 数量词 + 身体部位（句式骨架）
        "一只", "一个", "一只小", "两只", "两只小", "三只",
        "只小", "只小手", "小手", "小手合", "小手合力", "手合", "手合力",
        "小短", "短手", "小短手", "小腿", "短腿", "小短腿", "脑袋", "屁股", "身体", "面前",
        "肚皮", "肚子", "脚丫", "脚掌", "脸蛋", "眼睛", "嘴巴", "嘴角",
        "只菇", "整只菇", "整只",
        "合力", "一名", "着一", "在一", "一团", "为一", "正一", "是一",
        # 动作助词 / 副词修饰句式
        "正在", "正使", "正费", "正撅", "正歪", "正趴", "正瘫", "正躺", "正坐", "正站", "正合", "正合力",
        "正模", "正模仿", "秃正模",
        "试图", "费力", "费力地", "力地", "撅着", "歪着", "歪头", "趴在", "瘫在", "贴在", "靠在",
        "认真", "认真地", "极其", "非常", "极度", "嘿哟", "缓慢", "稳稳", "小心", "小心翼翼", "突然",
        "随着", "随风", "弯腰", "弯下", "似乎", "好像", "想要", "想把", "想给", "试图把",
        # 环境通用词（每条都会出现的环境感）
        "光线", "阳光", "微风", "风吹", "风从", "光影", "光斑", "气味", "空气", "氛围",
        # 形容自身大小的固定词（不是主题选择）
        "微缩", "小小", "巨大", "圆滚", "圆滚滚", "毛绒",
        # 常见过渡词
        "正是", "去把", "用力", "发力", "用劲", "极轻", "轻微", "轻轻", "缓缓", "默默",
        # 形容物件的修饰词（非主题）
        "落的", "掉落", "缘的",
        # 真正的 X 短语骨架（保留 "真正的" 主词，过滤碎片）
        "名真", "名真正", "一名真",
        # 模仿一名 短语骨架（保留 "模仿" 主词）
        "仿一", "仿一名", "模仿一",
    }
    filtered = [(w, c) for w, c in counter.most_common(top_n * 6) if w not in stopwords]
    return filtered[:top_n]


def sample_history_themes(themes: list[str], k: int = 150) -> list[str]:
    """从历史里**随机均匀采样** k 条，避免只看最新批次形成自我强化。"""
    if not themes:
        return []
    if len(themes) <= k:
        return list(themes)
    return random.sample(themes, k)


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

    # 历史样本/高频词警告已弃用：让 Claude 完全自主发散，避免 prompt 膨胀拖慢生成。
    history_block = ""

    # 每条蓝图预分配 scene (A) 或 IP (B)，user prompt 显式告诉 Claude 必须按指定的写
    mode_block = ""
    has_mode_hint = any("mode_hint" in bp for bp in blueprint_batch)
    if has_mode_hint:
        mode_lines = []
        for bp in blueprint_batch:
            cid = bp.get("context_id", "?")
            mh = bp.get("mode_hint", "A")
            if mh == "A":
                scene = bp.get("assigned_scene", "")
                desc = (
                    f"**A 类（真实生活）**：本条**必须**写在【{scene}】场景里发生的事；"
                    "动作必须是这个场景里真实会发生的小事，工具是该活动真实工具的微缩版；"
                    "严禁负面词（废弃/旧/烂/破/枯/锈/损坏等）；不要乱挑别的场景"
                )
            else:
                ip_info = bp.get("assigned_ip", "")
                desc = (
                    f"**B 类（IP 角色扮演）**：本条秃秃**必须**扮演 → 【{ip_info}】；"
                    "action_theme 第一句必须直接写出角色名和作品名，"
                    "并显式描述该角色的标志服饰/特征 + 把秃秃放在该角色的经典场景里做这个角色会做的事；"
                    "严禁乱挑别的角色，严禁泛化为'蒸汽朋克检修员'这种没有具体角色名的虚构世界设定"
                )
            mode_lines.append(f"- `{cid}`：{desc}")
        mode_block = (
            "**本批每条 context 的模式 + 预分配（必须严格遵守，不允许调换/换角色/换场景）**：\n"
            + "\n".join(mode_lines)
            + "\n\n"
        )

    # 去掉 LLM 不需要的重字段，避免 Sonnet 看到后回吐进 output 撑爆 maxTokens 导致 JSON 闭不上
    drop_keys = {"reference_hints", "trigger_priority", "pool_usage_policy", "mode_hint", "assigned_scene", "assigned_ip"}
    slim_batch = [
        {k: v for k, v in bp.items() if k not in drop_keys}
        for bp in blueprint_batch
    ]
    # 顶部硬性铁律提示——避免 Sonnet 忽略 system prompt 里的禁令
    iron_rules = (
        "**写作前必须确认的硬性铁律（违反必须重写）**：\n"
        "1. **全局禁负面词**：严禁出现'废弃/废墟/废土/遗迹/旧/烂/破/破败/破旧/腐/锈/锈迹/斑驳/残/残破/断裂/枯/枯萎/损坏/坏掉/烧焦/脏/发霉/灰暗/末日/丧/阴森/悲伤/绝望/暴力/血腥/恐怖'等任何负面词。秃秃的世界只允许美好、明亮、积极、温暖、可爱的画面。\n"
        "2. **B 类必须扮演经典 IP 角色（直接点名）**：当蓝图标记为 B 类时，秃秃必须扮演哈利·波特、孙悟空、皮卡丘、超级马里奥、海绵宝宝、葫芦娃、灰太狼、爱莎、钢铁侠、福尔摩斯、爱丽丝、林克、米老鼠、樱桃小丸子、哆啦A梦…等具体经典角色，第一句必须直接点名'秃秃今天扮演[角色名]，穿着[角色标志服饰]在[角色经典场景]...'。**严禁**写成'蒸汽朋克/赛博朋克空中城检修员'这种泛化虚构世界设定。\n"
        "2b. **B 类绝不允许改变秃秃本体**：身体颜色（仍是白底红波点蘑菇头/白色身体）、五官、形状不许变。**禁止**'染成红色/黄色/灰色'、'整个身体染成X色'、'长出尖耳朵/尾巴/翅膀/胡子/角/腮红'。所有角色特征必须靠**穿戴的装扮**呈现：连帽兜帽（耳朵/角/羽冠装帽子上）、罩衫/披风、面具、脸贴贴纸（眉毛/腮红/疤痕）、腰间挂件（尾巴是挂的不是长的）、标志道具（魔杖/剑/葫芦微缩版）。写法举例：'秃秃头戴黄色皮卡丘耳朵连帽兜帽（耳朵立在帽子两侧），脸颊贴着两枚红色腮红贴纸，腰间系着闪电尾巴挂件…' 而不是'整个身体染黄、长出尖耳朵'。\n"
        "3. **A 类同批不允许两条同类**：同一批 A 类的场景必须横跨完全不同的生活领域（已经有'共享单车'就不许再出'自行车/摩托车'，已经有'温室'就不许再出'植物园/花房'）。\n"
        "4. **B 类同批必须横跨 ≥4 个文化圈**：欧美电影/日漫/国漫国剧/游戏/童话至少覆盖 4 个，绝不允许两条同类角色。\n\n"
    )
    user_prompt = (
        f"{iron_rules}"
        f"{history_block}"
        f"{mode_block}"
        "请根据以下蓝图，生成同数量的 context：\n"
        "```json\n"
        f"{json.dumps(slim_batch, ensure_ascii=False, indent=2)}\n"
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


# 合理性检查：纯语法层面，只检测"A 模式声明 + 代用道具句式"这种明显矛盾
# 不维护家居词清单——剩下的合理性判断交给 Gemini 自查 + prompt 约束
A_CLAIM_PATTERN = re.compile(r"(真正的|作为一名|是一名|此刻[正]{0,1}是一{0,1}名|像[一]{0,1}名)")
PROP_SUBSTITUTE_PATTERN = re.compile(r"当作|当成|当[^前下时然要回不]{1,3}用|充当")


def check_action_theme_reasonableness(action_theme: str) -> str | None:
    """检测 A 类声明 + 代用道具句式的写法矛盾（v4：A=真实，B=虚拟世界，A 不能用代用道具）。

    返回 None 表示通过；返回 str 表示违规原因。

    只做最小语法层面的检查：声明了"作为/真正的/是一名 X"等真实身份短语，
    同时句中又出现"当作/当成/充当"等代用道具句式——A 类禁忌。
    """
    if not action_theme:
        return None
    if not A_CLAIM_PATTERN.search(action_theme):
        return None
    # 含 cosplay 词在 v4 里也不允许（A 不写假扮，B 是虚拟世界一员也不写"模仿"）
    has_cosplay = any(w in action_theme for w in ["模仿", "扮演", "假装", "装作", "像一样"])
    if has_cosplay:
        return f"含'模仿/扮演/假装/装作'等假扮词——v4 已取消 cosplay 模式，A 类是真做事 / B 类是虚拟世界一员，都不允许假扮"
    # A 类声明 + 代用道具句式 → 矛盾
    if PROP_SUBSTITUTE_PATTERN.search(action_theme):
        return f"含'{A_CLAIM_PATTERN.search(action_theme).group()}'+代用道具句式（当作/当成/充当）矛盾"
    return None


def extract_theme_triplet(action_theme: str) -> tuple[str, str]:
    """提取 action_theme 的简单"动词+核心物件"指纹，用于同批查重。

    粗暴做法：把所有 2-3 字汉字片段做成集合的代表词。返回 (主动词, 主物件) 近似元组。
    我们不严格——只要两条 action_theme 的"前 8 个 2-字片段集合"重合度 ≥75%，就视为重复。
    """
    if not action_theme:
        return ("", "")
    clean = re.sub(r"[^一-龥]+", " ", action_theme)
    chunks: list[str] = []
    for ch in clean.split():
        for i in range(len(ch) - 1):
            chunks.append(ch[i : i + 2])
        if len(chunks) >= 12:
            break
    return tuple(chunks[:12])  # type: ignore


def find_intra_batch_duplicates(action_themes: list[str]) -> list[tuple[int, int, float]]:
    """同批内寻找高相似度对。返回 [(i, j, similarity), ...]，相似度 ≥0.55 视为重复。"""
    fingerprints = [set(extract_theme_triplet(t)) for t in action_themes]
    dups: list[tuple[int, int, float]] = []
    for i in range(len(fingerprints)):
        for j in range(i + 1, len(fingerprints)):
            a, b = fingerprints[i], fingerprints[j]
            if not a or not b:
                continue
            inter = len(a & b)
            union = len(a | b)
            if union == 0:
                continue
            jacc = inter / union
            if jacc >= 0.55:
                dups.append((i, j, jacc))
    return dups


def _process_one_batch(
    batch: list[dict[str, Any]],
    seen_themes: list[str],
) -> list[dict[str, Any]]:
    """处理单个批次（带重试）。返回该批次的 contexts 列表。

    重试触发条件：
    - call_llm 抛异常（gateway 504、IncompleteRead、解析失败等）
    - 拿到响应但批次内有任何 context_id 没有合法返回
    - 拿到响应但有 action_theme 违规 / 同批重复
    """
    max_attempts = 5
    batch_contexts: list[dict[str, Any]] = []
    bad_examples: list[tuple[str, str]] = []
    batch_ids = [bp["context_id"] for bp in batch]
    for attempt in range(max_attempts):
        extra_hint = ""
        if bad_examples:
            extra_hint = (
                "\n\n**上一轮你输出了下列离谱的 A 模式条目（声明真实身份但场景/工具是家居代用），这次必须避免**：\n"
                + "\n".join(f"- ❌ {t}（违规原因：{r}）" for t, r in bad_examples[-10:])
                + "\n请重写所有 action_theme：A 模式必须真实场景+真专业工具，不许把家居小物件包装成专业 A 写法。\n"
            )
        system_prompt, user_prompt = build_context_generation_prompts(batch, previous_themes=seen_themes)
        user_prompt = user_prompt + extra_hint
        try:
            raw = call_llm(system_prompt, user_prompt)
        except Exception as exc:  # noqa: BLE001
            print(f"[phase-a-call-fail] attempt {attempt+1}/{max_attempts} batch {batch_ids}: {exc}", flush=True)
            time.sleep(min(2.0 * (attempt + 1), 8.0))
            continue
        parsed = parse_json_block(raw, default=[])
        if not isinstance(parsed, list):
            parsed = []

        by_id = {item["context_id"]: item for item in batch}
        attempt_contexts: list[dict[str, Any]] = []
        new_bad: list[tuple[str, str]] = []
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
            try:
                validate_context(merged)
            except ValueError as exc:
                print(f"[phase-a-skip] {merged.get('context_id', '?')}: {exc}", flush=True)
                continue
            reason = check_action_theme_reasonableness(str(merged.get("action_theme", "")))
            if reason:
                new_bad.append((str(merged["action_theme"]), reason))
            attempt_contexts.append(merged)

        themes_in_batch = [str(c.get("action_theme", "")) for c in attempt_contexts]
        dups = find_intra_batch_duplicates(themes_in_batch)
        if dups:
            seen_idx = set()
            for i, j, jacc in dups:
                if j not in seen_idx:
                    new_bad.append((themes_in_batch[j], f"同批重复（与第 {i+1} 条 jaccard={jacc:.2f}）"))
                    seen_idx.add(j)

        # 只保留这一轮里"成功且无违规"的条目
        if attempt_contexts:
            batch_contexts = attempt_contexts

        returned_ids = {ctx["context_id"] for ctx in batch_contexts}
        missing = [cid for cid in batch_ids if cid not in returned_ids]

        # 通过条件：所有 context_id 都拿到了，并且没有违规
        if not missing and not new_bad:
            break

        # 仍有缺失或违规 → 重试
        if missing:
            print(f"[phase-a-empty] attempt {attempt+1}/{max_attempts} batch {batch_ids}: 缺失 {missing}", flush=True)
        if new_bad:
            print(f"[phase-a-retry] attempt {attempt+1}/{max_attempts} batch {batch_ids}: {len(new_bad)} 条违规", flush=True)
        bad_examples = new_bad
        time.sleep(min(1.0 * (attempt + 1), 4.0))

    # 补齐没返回的（用 fallback）
    returned_ids = {ctx["context_id"] for ctx in batch_contexts}
    for fallback in batch:
        if fallback["context_id"] not in returned_ids:
            print(f"[phase-a-FALLBACK] {fallback['context_id']} 用了模板兜底（{max_attempts} 次都没成功）", flush=True)
            fb_ctx = build_context_fallback(fallback)
            try:
                validate_context(fb_ctx)
                batch_contexts.append(fb_ctx)
            except ValueError:
                pass
    return batch_contexts


def generate_contexts_with_gemini(blueprints: list[dict[str, Any]], batch_size: int) -> list[dict[str, Any]]:
    """并行处理所有批次。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    history_themes = load_history_action_themes()
    print(f"[phase-a] loaded {len(history_themes)} history action_themes as anti-repetition examples", flush=True)

    batches = chunks(blueprints, batch_size)
    n_workers = int(os.environ.get("PHASE_A_WORKERS", "10"))
    n_workers = max(1, min(n_workers, len(batches)))
    print(f"[phase-a] processing {len(batches)} batches in parallel (workers={n_workers})", flush=True)

    all_contexts: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {
            pool.submit(_process_one_batch, batch, list(history_themes)): batch
            for batch in batches
        }
        completed = 0
        for fut in as_completed(futures):
            batch_ctxs = fut.result()
            all_contexts.extend(batch_ctxs)
            completed += 1
            if completed % 5 == 0 or completed == len(futures):
                print(f"[phase-a] {completed}/{len(futures)} batches done ({len(all_contexts)} contexts)", flush=True)
    # 按蓝图原始顺序排序
    order = {bp["context_id"]: i for i, bp in enumerate(blueprints)}
    all_contexts.sort(key=lambda c: order.get(c["context_id"], 0))
    return all_contexts


def generate_contexts_with_gemini_OLD_SEQUENTIAL(blueprints: list[dict[str, Any]], batch_size: int) -> list[dict[str, Any]]:
    """旧的顺序版本，保留作为参考；现已被并行版本取代。"""
    all_contexts: list[dict[str, Any]] = []
    history_themes = load_history_action_themes()
    print(f"[phase-a] loaded {len(history_themes)} history action_themes as anti-repetition examples", flush=True)
    for batch in chunks(blueprints, batch_size):
        seen_themes = list(history_themes) + [c.get("action_theme", "") for c in all_contexts]
        max_attempts = 3
        batch_contexts: list[dict[str, Any]] = []
        bad_examples: list[tuple[str, str]] = []
        for attempt in range(max_attempts):
            extra_hint = ""
            if bad_examples:
                extra_hint = (
                    "\n\n**上一轮你输出了下列离谱的 A 模式条目（声明真实身份但场景/工具是家居代用），这次必须避免**：\n"
                    + "\n".join(f"- ❌ {t}（违规原因：{r}）" for t, r in bad_examples[-10:])
                    + "\n请重写所有 action_theme：A 模式必须真实场景+真专业工具，不许把家居小物件包装成专业 A 写法。\n"
                )
            system_prompt, user_prompt = build_context_generation_prompts(batch, previous_themes=seen_themes)
            user_prompt = user_prompt + extra_hint
            raw = call_llm(system_prompt, user_prompt)
            parsed = parse_json_block(raw, default=[])
            if not isinstance(parsed, list):
                parsed = []

            by_id = {item["context_id"]: item for item in batch}
            batch_contexts = []
            new_bad: list[tuple[str, str]] = []
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
                # 容错：Gemini 偶发返回某些字段为空，这种条目跳过而不是 crash 整批
                try:
                    validate_context(merged)
                except ValueError as exc:
                    print(f"[phase-a-skip] {merged.get('context_id', '?')}: {exc}", flush=True)
                    continue
                # 合理性检查
                reason = check_action_theme_reasonableness(str(merged.get("action_theme", "")))
                if reason:
                    new_bad.append((str(merged["action_theme"]), reason))
                batch_contexts.append(merged)

            # 同批三元组相似度查重（Jaccard ≥0.55 视为重复）
            themes_in_batch = [str(c.get("action_theme", "")) for c in batch_contexts]
            dups = find_intra_batch_duplicates(themes_in_batch)
            if dups:
                # 把每对里编号大的那条标违规，让 Gemini 改写
                seen_idx = set()
                for i, j, jacc in dups:
                    if j not in seen_idx:
                        new_bad.append((themes_in_batch[j], f"同批重复（与第 {i+1} 条 jaccard={jacc:.2f}）"))
                        seen_idx.add(j)

            if not new_bad:
                break  # 全部合规
            bad_examples = new_bad
            print(f"[phase-a-retry] batch attempt {attempt+1}: {len(new_bad)} 条违规，重新生成", flush=True)
            for t, r in new_bad[:3]:
                print(f"    - {r}: {t[:80]}", flush=True)
        else:
            print(f"[phase-a-warn] {max_attempts} 次后仍有 {len(bad_examples)} 条违规，接受现状", flush=True)

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
