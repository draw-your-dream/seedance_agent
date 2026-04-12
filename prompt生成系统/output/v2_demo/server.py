"""
秃秃 IP Agent v2 Demo
- 视频时间线从预生成的 JSON 读取，秒开
- 聊天调 LLM，互动影响秃秃生活
"""
import os, json, time, random, sys
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, send_file

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from tutu_core.llm_client import call_llm, extract_json

app = Flask(__name__)

VIDEO_DIR = os.path.join(os.path.dirname(__file__), "..", "videos")
DATA_FILE = os.path.join(os.path.dirname(__file__), "timeline_data.json")

# Load pre-generated timeline
with open(DATA_FILE, "r") as f:
    timeline_days = json.load(f)

# Flatten all slots for reference
all_videos = set()
for day in timeline_days:
    for slot in day["slots"]:
        all_videos.add(slot["video"])

# Available videos not yet in timeline (for new events)
all_video_files = [f for f in os.listdir(VIDEO_DIR) if f.endswith(".mp4")] if os.path.exists(VIDEO_DIR) else []
unused_videos = [f for f in all_video_files if f not in all_videos]

memory = {
    "nickname": "小薯条",
    "preferences": ["美食", "户外"],
    "key_moments": [],
    "interactions_today": [],
}


def chat_with_tutu(user_message):
    # Current day events
    current_day = timeline_days[0] if timeline_days else {"slots": []}
    events = [f"{s['time']} {s['title']}" for s in current_day.get("slots", [])[:5]]
    interactions_str = "\n".join(memory["interactions_today"][-5:]) or "无"
    key_moments_str = "\n".join(memory["key_moments"][-3:]) or "无"

    # Available videos for new event
    avail_desc = ""
    if unused_videos:
        samples = random.sample(unused_videos, min(5, len(unused_videos)))
        avail_desc = "\n".join([f"- {v}" for v in samples])

    prompt = f"""你是秃秃，4cm高的小蘑菇，好奇、有点笨、可爱。回复用中文写。你吃草。

【今天做了】
{chr(10).join(events)}
【用户】昵称：{memory['nickname']}，偏好：{', '.join(memory['preferences'])}
【关键记忆】{key_moments_str}
【今日互动】{interactions_str}
【用户说】{user_message}

{f"【可选新活动视频】{chr(10)}{avail_desc}" if avail_desc else ""}

回复2句，短、可爱。判断是否有新偏好/记忆，是否想去做新的事。
输出JSON：
{{"reply":"回复","memory_update":{{"new_preference":"或null","key_moment":"或null"}},"new_event":{{"video":"文件名或null","title":"短标题或null"}}}}"""

    try:
        raw = call_llm("", prompt, max_tokens=400)
        if not raw:
            return "嘟？（歪头看着你）", None

        result = extract_json(raw)
        memory["interactions_today"].append(f"{time.strftime('%H:%M')} {user_message}")

        if result.get("memory_update"):
            mu = result["memory_update"]
            if mu.get("new_preference") and mu["new_preference"] not in memory["preferences"]:
                memory["preferences"].append(mu["new_preference"])
            if mu.get("key_moment"):
                memory["key_moments"].append(f"{time.strftime('%m月%d日')} {mu['key_moment']}")

        new_event = None
        if result.get("new_event") and result["new_event"].get("video"):
            vid = result["new_event"]["video"]
            if os.path.exists(os.path.join(VIDEO_DIR, vid)):
                new_slot = {
                    "time": time.strftime('%H:%M'),
                    "period": "刚刚",
                    "weather": "✨",
                    "title": result["new_event"].get("title", vid),
                    "video": vid,
                    "thoughts": [
                        {"time": time.strftime('%H:%M'), "text": "因为你说的！去看看"}
                    ],
                    "is_new": True,
                    "triggered_by": user_message,
                }
                if timeline_days:
                    timeline_days[0]["slots"].insert(0, new_slot)
                new_event = {"title": new_slot["title"], "video_url": f"/videos/{vid}"}
                if vid in unused_videos:
                    unused_videos.remove(vid)

        return result.get("reply", "嘟？"), new_event
    except Exception as e:
        print(f"Chat error: {e}")
        return "嘟？（歪头看着你）", None


@app.route("/")
def index():
    return send_file("app.html")

@app.route("/api/timeline")
def get_timeline():
    result = []
    for day in timeline_days:
        day_data = {"date": day["date"], "weekday": day["weekday"], "slots": []}
        for s in day["slots"]:
            day_data["slots"].append({
                "time": s["time"],
                "period": s["period"],
                "weather": s["weather"],
                "title": s["title"],
                "video_url": f"/videos/{s['video']}",
                "thoughts": s["thoughts"],
                "is_new": s.get("is_new", False),
                "triggered_by": s.get("triggered_by"),
            })
        result.append(day_data)
    return jsonify(result)

@app.route("/api/chat", methods=["POST"])
def chat():
    msg = request.json.get("message", "").strip()
    if not msg:
        return jsonify({"reply": "嘟？"})
    reply, new_event = chat_with_tutu(msg)
    resp = {"reply": reply, "memory": memory}
    if new_event:
        resp["new_event"] = new_event
    return jsonify(resp)

@app.route("/api/memory")
def get_memory():
    return jsonify(memory)

@app.route("/videos/<path:filename>")
def serve_video(filename):
    return send_from_directory(VIDEO_DIR, filename)

if __name__ == "__main__":
    print("\n🍄 秃秃 IP Agent v2 Demo")
    print(f"  http://localhost:5555")
    print(f"  {len(timeline_days)} 天, {sum(len(d['slots']) for d in timeline_days)} 条视频\n")
    app.run(host="0.0.0.0", port=5555, debug=False)
