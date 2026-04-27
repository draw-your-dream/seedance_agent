import sys, time, json
sys.path.insert(0, ".")
import phase_a

# 测试 1: 小 JSON
t = time.time()
r = phase_a.call_claude(
    "你输出 JSON 数组，每个元素是 {\"i\": 数字}",
    '请返回这个：[{"i":1},{"i":2},{"i":3}]',
)
elapsed1 = time.time() - t
print(f"test1 ({elapsed1:.1f}s): len={len(r)}")
print("末尾:", repr(r[-200:]))
print()

# 测试 2: 中等大小（生成 5 个长 context 字段）
t = time.time()
r2 = phase_a.call_claude(
    "你是 prompt 生成助手，根据要求生成 JSON 数组",
    '请生成 5 个 JSON 对象的数组，每个对象有字段 background (50字)、action_theme (40字)、mood_theme (10字)。直接输出 JSON 数组：',
)
elapsed2 = time.time() - t
print(f"test2 ({elapsed2:.1f}s): len={len(r2)}")
print("末尾:", repr(r2[-300:]))
