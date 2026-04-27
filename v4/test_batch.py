import sys, time, json
sys.path.insert(0, ".")
import phase_a

# 真实模拟 Phase A 一个 batch=5 调用
fake_blueprints = []
for i in range(5):
    fake_blueprints.append({
        "context_id": f"test_{i+1:05d}",
        "run_label": "test",
        "slot": ["morning", "late_morning", "afternoon", "golden_hour", "night"][i],
        "slot_time_hint": ["08:30", "10:30", "14:30", "17:30", "21:00"][i],
        "weather": "晴，有风，24度",
        "season": "初夏",
        "solar_term": "立夏后",
        "reference_hints": {"daily": f"hint{i}"},
        "trigger_priority": "medium",
        "background": "", "lifestyle_theme": "", "action_theme": "", "mood_theme": "",
    })

sys_p, user_p = phase_a.build_context_generation_prompts(fake_blueprints, previous_themes=[])
print(f"system prompt: {len(sys_p)} chars")
print(f"user prompt: {len(user_p)} chars")
print(f"total input: {len(sys_p) + len(user_p)} chars")
print()

t = time.time()
raw = phase_a.call_claude(sys_p, user_p)
elapsed = time.time() - t
print(f"Claude returned in {elapsed:.1f}s, {len(raw)} chars")
print()
print("=== Raw response (first 500 chars) ===")
print(raw[:500])
print()
print("=== Raw response (last 500 chars) ===")
print(raw[-500:])
print()
print("=== Try parse ===")
parsed = phase_a.parse_json_block(raw, default=None)
if parsed is None:
    print("PARSE FAILED")
elif isinstance(parsed, list):
    print(f"PARSED OK: list of {len(parsed)} items")
    for item in parsed[:2]:
        print("  -", json.dumps(item, ensure_ascii=False)[:100])
else:
    print(f"PARSED unexpected type: {type(parsed)}")
