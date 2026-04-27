"""Dump v4 Phase A 实际喂给 Gemini 的完整 prompt 到 ../v4_prompt_demo.md"""
import glob
import json
import random
import sys

sys.path.insert(0, ".")
import phase_a

themes = []
for path in sorted(glob.glob("../tutu多sku文生视频流/outputs/phase_a/*/phase_a_contexts.jsonl")):
    for line in open(path, encoding="utf-8-sig"):
        if line.strip():
            r = json.loads(line)
            t = r.get("action_theme", "").strip()
            if t:
                themes.append(t)

fake = [{
    "context_id": "demo_001", "run_label": "v4_demo",
    "slot": "morning", "slot_time_hint": "08:30",
    "weather": "晴，有风，24度", "season": "初夏", "solar_term": "立夏后",
    "reference_hints": {"daily": "morning hint", "weather": "晴 hint"},
    "trigger_priority": "medium",
    "background": "", "lifestyle_theme": "", "action_theme": "", "mood_theme": "",
}]

random.seed(42)
sys_p, user_p = phase_a.build_context_generation_prompts(fake, previous_themes=themes)

out = []
out.append("# v4 Phase A Prompt 完整 dump（喂给 Gemini 的实际内容）\n")
out.append(f"- 历史 themes 数量：{len(themes)}")
out.append(f"- system prompt: {len(sys_p)} 字符")
out.append(f"- user prompt: {len(user_p)} 字符\n")
out.append("---\n")
out.append("## SYSTEM PROMPT\n")
out.append("```\n" + sys_p + "\n```\n")
out.append("## USER PROMPT\n")
out.append("```\n" + user_p + "\n```\n")

with open("../v4_prompt_demo.md", "w", encoding="utf-8") as f:
    f.write("\n".join(out))

print(f"wrote {len(sys_p) + len(user_p)} chars to ../v4_prompt_demo.md")
