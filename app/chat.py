# -*- coding: utf-8 -*-
"""聊天服务 — LLM驱动，秃秃口吻回复"""

import json
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tutu_core.config import PERSONALITY_FILE
from tutu_core.llm_client import call_llm, extract_json

import database as db


# personality 缓存：避免每次聊天都读磁盘
_personality_cache = {"text": None, "mtime": 0}


def _load_personality():
    """带缓存的 personality 加载，文件变更后自动刷新。"""
    if not PERSONALITY_FILE.exists():
        return ""
    mtime = PERSONALITY_FILE.stat().st_mtime
    if _personality_cache["text"] is None or mtime != _personality_cache["mtime"]:
        _personality_cache["text"] = PERSONALITY_FILE.read_text(encoding="utf-8")
        _personality_cache["mtime"] = mtime
    return _personality_cache["text"]


def chat_reply(user_message, image_desc=None):
    """
    处理用户消息，返回秃秃的回复。
    返回: {"reply": str, "trigger_video": bool, "video_desc": str}
    """
    personality = _load_personality()

    # 获取上下文
    today_events = db.get_today_events()
    current_activity = ""
    if today_events:
        latest = today_events[-1]
        current_activity = f"秃秃现在在{latest['title']}（{latest['time']}）"

    chat_history = db.get_chat_history(limit=10)
    history_text = ""
    for msg in chat_history[-8:]:
        who = "用户" if msg["sender"] == "user" else "秃秃"
        history_text += f"{who}：{msg['content']}\n"

    triggered_today = sum(1 for e in today_events if e.get("triggered_by") == "user")

    user_input = user_message
    if image_desc:
        user_input += f"\n（用户还发了一张图片：{image_desc}）"

    can_trigger = triggered_today < 2
    trigger_note = "" if can_trigger else f"（今天已触发{triggered_today}条视频，不要再触发新的）"

    system_prompt = f"""{personality}

你是秃秃，一个4cm高的小蘑菇。你要以秃秃的身份回复用户的消息。
回复要有秃秃的性格：嘴硬但在乎用户。"""

    user_prompt = f"""当前状态：{current_activity or '秃秃在家待着'}
最近对话：
{history_text}

用户新消息：{user_input}

请以JSON格式回复：
```json
{{
  "reply": "秃秃的回复（30-80字，口语化，偶尔用嘟）",
  "trigger_video": true或false,
  "video_desc": "如果trigger_video为true，描述秃秃会因为这条消息做什么（一句话）"
}}
```

规则：
· 如果用户分享了图片或有趣的内容，trigger_video设为true
· 如果用户只是闲聊/打招呼，trigger_video设为false
· 如果用户表达了情绪（累/开心/难过），可以trigger_video做回应
· video_desc要具体到秃秃做什么动作
{trigger_note}"""

    raw = call_llm(system_prompt, user_prompt, use_cache=False)

    if not raw:
        return {"reply": "嘟？", "trigger_video": False, "video_desc": ""}

    try:
        data = extract_json(raw)
        trigger = bool(data.get("trigger_video", False)) and can_trigger
        return {
            "reply": data.get("reply", "嘟？"),
            "trigger_video": trigger,
            "video_desc": data.get("video_desc", "") if trigger else ""
        }
    except (json.JSONDecodeError, ValueError):
        # fallback：清理 LLM 输出中的 markdown 残留，安全截断
        clean = raw.replace("```json", "").replace("```", "").strip()
        # 安全截断：不在多字节字符中间截断
        reply = clean[:100] if len(clean) <= 100 else clean[:100].rsplit("，", 1)[0] or clean[:80]
        return {"reply": reply, "trigger_video": False, "video_desc": ""}
