# -*- coding: utf-8 -*-
"""
内容生成核心逻辑 — 日程生成 + 视频内容生成

从 scheduler.py 和 life_engine.py 的重复代码中提取统一实现。
两者均调用本模块，不再各自维护 prompt 模板。
"""

import json
import logging

from tutu_core.config import PERSONALITY_FILE, IP_CONSTITUTION_FILE
from tutu_core.llm_client import call_llm, extract_json

logger = logging.getLogger("tutu.generation")


# personality / constitution 缓存
_file_cache: dict[str, tuple[float, str]] = {}


def _load_cached(path) -> str:
    """带 mtime 缓存的文件读取。"""
    key = str(path)
    if not path.exists():
        return ""
    mtime = path.stat().st_mtime
    if key in _file_cache and _file_cache[key][0] == mtime:
        return _file_cache[key][1]
    text = path.read_text(encoding="utf-8")
    _file_cache[key] = (mtime, text)
    return text


def generate_schedule(
    date_str: str,
    weather: str = "",
    user_city: str = "",
    hot_signals: str = "",
    interactions: str = "",
    journal: str = "",
) -> list[dict]:
    """
    生成秃秃一天的日程（3-5条事件）。

    返回: [{"time", "title", "summary", "triggered_by", "user_related"}, ...]
    """
    personality = _load_cached(PERSONALITY_FILE)
    constitution = _load_cached(IP_CONSTITUTION_FILE)

    system_prompt = f"""{personality}

{constitution}

你现在要规划秃秃今天的生活。你需要输出3-5个事件，每个事件有具体时间。

规则：
1. 秃秃有自己的生活节奏（早上起床、日间活动、晚上睡觉），但会被用户互动强烈影响
2. 如果用户最近发了消息或图片，至少1-2条事件要跟用户的输入有关联
3. 如果用户发了图片描述，秃秃可以直接做相关的事
4. 如果用户3天没互动，有一条事件是秃秃偷偷看向镜头等人
5. 天气和热点可以影响1条事件
6. 不要重复最近7天做过的事情
7. 情绪范围：小开心/小好奇/小委屈/小倔强/小满足，不要有暴怒/大哭等激烈情绪
8. 不要有吃东西的画面"""

    user_prompt = f"""今天是 {date_str}
天气：{weather or '晴，22度'}
用户所在城市：{user_city or '未知'}
当日热点：{hot_signals or '无特别热点'}

用户最近的互动：
{interactions}

秃秃最近的生活：
{journal}

请输出今天秃秃的日程，严格按JSON格式，不要输出其他内容：
```json
[
  {{
    "time": "08:30",
    "title": "事件标题（简短）",
    "summary": "一句话描述这个事件",
    "triggered_by": "daily/user/weather/hotspot",
    "user_related": true/false
  }}
]
```"""

    raw = call_llm(system_prompt, user_prompt)
    if not raw:
        return []
    try:
        return extract_json(raw, expect_array=True)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"日程JSON解析失败: {e}")
        return []


def generate_event_content(
    event: dict,
    date_str: str,
    interactions: str = "",
) -> dict | None:
    """
    为单个事件生成视频 prompt + 心理活动。

    返回: {"video_prompt", "inner_voice", "thoughts"} 或 None
    """
    personality = _load_cached(PERSONALITY_FILE)

    system_prompt = f"""{personality}

你需要为秃秃的一个生活事件生成两样东西：
1. 视频prompt（给Seedance视频模型）
2. 心理活动文案（菇的碎碎念，30-60字）

视频prompt规则：
· 必须以"图片1是小蘑菇角色形象参考。"开头
· 微缩场景，小蘑菇只有4cm高，构图中近景，角色不要太大不要超过画面三分之一
· 包含4段时间码：0-3s / 3-7s / 7-10s / 10-13s
· 每段带1句音效描写
· 结尾有互动beat（看镜头/画面定格）
· 末尾必须写："只要音效，不要背景音乐，不要字幕。注意：小蘑菇没有牙齿、没有舌头、没有眉毛、没有尾巴。"
· 总字数控制在500-700字
· 情绪上限：小委屈/小惊讶/小倔强，不能有暴怒/打砸等激烈动作
· 不要有吃东西的画面
· 角色没有手指和牙齿

心理活动(inner_voice)规则：
· 30-60字，第一人称，口语化
· 如果这个事件跟用户有关，要自然提到"你"

碎碎念(thoughts)规则：
· 2-3条短句，每条10-25字，带具体时间
· 是inner_voice的拆分版，更零碎更随性"""

    user_prompt = f"""日期：{date_str}
事件时间：{event['time']}
事件标题：{event['title']}
事件描述：{event.get('summary', '')}
是否与用户相关：{event.get('user_related', False)}
触发来源：{event.get('triggered_by', 'daily')}

用户最近互动：
{interactions}

请严格按以下JSON格式输出：
```json
{{
  "video_prompt": "图片1是小蘑菇角色形象参考。......",
  "inner_voice": "菇的碎碎念文案......",
  "thoughts": [{{"time": "{event['time']}", "text": "短句1"}}, {{"time": "{event['time']}", "text": "短句2"}}]
}}
```"""

    raw = call_llm(system_prompt, user_prompt, max_tokens=2000)
    if not raw:
        return None
    try:
        content = extract_json(raw)
        if not content.get("video_prompt", "").startswith("图片1"):
            return None
        # 如果 LLM 没生成 thoughts，从 inner_voice 自动生成一条
        voice = content.get("inner_voice", "")
        if not content.get("thoughts") and voice:
            content["thoughts"] = [{"time": event["time"], "text": voice[:60]}]
        return content
    except (json.JSONDecodeError, ValueError):
        return None
