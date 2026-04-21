# 秃秃视频生成 Pipeline 运行指南

本文档描述 v1+ 和 v2 两版 prompt 生成 pipeline 的结构、差异与运行方法。
两个版本共享相同的参考图体系和提交流程，区别在 LLM system_prompt 的严苛度。

---

## 目录

1. [总体架构](#1-总体架构)
2. [参考图体系](#2-参考图体系)
3. [v1+ 与 v2 的区别](#3-v1-与-v2-的区别)
4. [运行方式](#4-运行方式)
5. [输出产物](#5-输出产物)
6. [故障排查](#6-故障排查)

---

## 1. 总体架构

```
事件（title / summary / time）
        │
        ▼
┌───────────────────────────────────┐
│ generation_router (选 v1+ 或 v2)   │
│   依据 GENERATION_VERSION env     │
└───────────────┬───────────────────┘
                │
                ▼
┌───────────────────────────────────┐
│ generate_event_content            │
│   classify_event (6 类之一)       │
│   load few-shot example            │
│   调 LLM 生成 prompt（含占位符）   │
│   quality_review 校验+重试        │
└───────────────┬───────────────────┘
                │
         LLM 原始 prompt
                │
                ▼
┌───────────────────────────────────┐
│ submit_task（seedance_client）     │
│                                   │
│ 1. match_expressions              │
│    扫描关键词 + {emotion} 占位符   │
│    得 matched = [happy, cry, ...] │
│                                   │
│ 2. resolve_expression_placeholders│
│    {happy} → 图片6                │
│    {cry}   → 图片7                │
│                                   │
│ 3. inject_image_declaration       │
│    剥离 LLM 残留的"图片1是..."声明 │
│    规则生成完整声明段贴在开头     │
│                                   │
│ 4. 加载对应图片（多张 reference）  │
│ 5. 提交 Seedance API              │
│ 6. 归档 prompt 到磁盘              │
└───────────────┬───────────────────┘
                │
              task_id
                │
                ▼
          轮询 + 下载 mp4
```

---

## 2. 参考图体系

### 2.1 固定 5 张（永远上传）

| 编号 | 用途 | 文件 |
|------|------|------|
| 图片1 | 主参考图（角色形象） | `ref/processed/reference.jpg` |
| 图片2 | 肢体末端特写（圆形无爪子） | `ref/processed/hand_closeup.jpg` |
| 图片3 | 张嘴表情（嘴内黑色） | `ref/processed/mouth_closeup.jpg` |
| 图片4 | 屁股特写 | `ref/processed/back.jpg` |
| 图片5 | 全身比例参考 | `ref/processed/full_body.jpg` |

### 2.2 条件 5 张（按 prompt 内容动态选）

| 占位符 | 中文 | 文件 |
|--------|------|------|
| `{happy}` | 开心 | `ref/processed/expressions/happy.jpg` |
| `{cry}`   | 委屈哭泣 | `ref/processed/expressions/cry.jpg` |
| `{shy}`   | 害羞 | `ref/processed/expressions/shy.jpg` |
| `{angry}` | 生气奶凶 | `ref/processed/expressions/angry.jpg` |
| `{laugh}` | 大笑 | `ref/processed/expressions/laugh.jpg` |

### 2.3 占位符工作流

**LLM 输出**（只用占位符，不写图片编号）：
```
0-3s：秃秃眯眼笑（参考{happy}表情图），腮帮子鼓鼓。
3-7s：突然眼泪在眼眶里（参考{cry}情绪图）。
```

**规则处理**：
1. `match_expressions` 扫描 prompt → 得到 `["happy", "cry"]`（按 `EXPRESSION_KEYWORDS` 声明顺序）
2. 按匹配顺序分配编号：`happy=图片6`、`cry=图片7`
3. 替换占位符 → `参考图片6表情图` / `参考图片7情绪图`
4. 在 prompt 开头注入完整声明段：
   ```
   图片1是小蘑菇角色形象参考。图片2是肢体末端...图片5是全身比例参考。
   图片6是「开心」表情参考。图片7是「委屈哭泣」表情参考。
   描述动作或表情时可以显式引用对应图片。
   ```

### 2.4 关键词触发（兜底）

即使 LLM 忘用占位符，只要正文出现情绪关键词（如"眯眼笑"/"委屈"）也会触发相应表情图附加（`match_expressions` 取占位符 ∪ 关键词的并集）。

---

## 3. v1+ 与 v2 的区别

两版都**继承**：
- 参考图体系（5 固定 + 5 条件）
- 占位符工作流
- IP 铁律（肢体末端 / 嘴内黑色 / 禁止说话 / 不要"手"这个词）
- 视觉风格块（日系写实 / 浅景深 / 胶片颗粒 / 暖色调 / 时段光线 / 中心对称 / 比例参照 / 背景虚化）

**区别**在 LLM system_prompt 的额外要求：

| 维度 | v1+ | v2 |
|------|-----|----|
| 分镜时长 | 13 秒 4 段（0-3 / 3-7 / 7-10 / 10-13） | 15 秒 5-6 段（起承转合） |
| 音效 | 嵌在分镜内 | **独立 "配乐/音效：" 段** |
| 动作描写 | 具象要求 | **强制"身体一点一点"级细节** + 叠词密度 ≥ 1 |
| 收尾 | 互动 beat（看镜头/眨眼） | **"画面温柔定格" + 极轻极满足的嘟——** |
| 字数 | 500-700 | 600-900 |
| 质量校验 | v1 基础 7 项 | v1 + 4 项（风格标签/构图/收尾/叠词） |

**选型建议**：
- v1+：兼容原有 Seedance 提交，适合**已验证通过的正式生产**
- v2：风格更一致，接近 example_prompts 的水准，适合**追求统一美学**的场景（但要求更严格，可能重试次数多）

---

## 4. 运行方式

### 4.1 环境变量

```
.env 必需：
  ARK_API_KEY=xxx       # Seedance 视频生成 + ARK LLM 两用
  GEMINI_API_KEY=xxx    # Gemini LLM
  GEMINI_URL=https://ai.ssnai.com/gemini/v1beta/models/gemini-2.5-flash:generateContent

.env 可选：
  LLM_PROVIDER=gemini           # gemini / ark / claude / openai
  GENERATION_VERSION=v1         # v1 / v2 （默认 v1）
  ADMIN_API_KEY=xxx             # FastAPI admin 鉴权
  POLL_INTERVAL=120             # 后台轮询秒数
  LOG_LEVEL=INFO                # DEBUG / INFO / WARNING / ERROR
```

### 4.2 单条生成（Python API）

```python
from tutu_core.generation_router import generate_event_content
from tutu_core.seedance_client import (
    load_reference_images_for_prompt, submit_task,
)

# 事件
evt = {
    "time": "10:30",
    "title": "秃秃第一次认识订书机",
    "summary": "桌上发现银色订书机，按了一下被弹飞",
    "triggered_by": "daily",
    "user_related": False,
}

# 1. 生成 prompt（v1+/v2 由 GENERATION_VERSION 决定，或显式指定）
content = generate_event_content(evt, "2026-04-21", version="v1")  # 或 version="v2"
raw_prompt = content["video_prompt"]

# 2. 加载参考图（匹配 + 加载）
img_b64, labels = load_reference_images_for_prompt(raw_prompt)

# 3. 提交 Seedance（submit_task 内部会自动 resolve 占位符 + 注入声明 + 归档 prompt）
task_id, err = submit_task(raw_prompt, img_b64, payload_tag="manual_test")
print(f"task_id: {task_id}")
```

### 4.3 批量生成（参考 `tests/ab_compare.py` 结构）

典型脚本骨架：
```python
import sys, json, time
sys.path.insert(0, '/workspace/tutu内容')
from tutu_core.generation_router import generate_event_content
from tutu_core.seedance_client import load_reference_images_for_prompt, submit_task

events = [
    {"time": "07:30", "title": "秃秃赖床", "summary": "闹钟响了..."},
    # ...
]

tasks = []
for evt in events:
    content = generate_event_content(evt, "2026-04-21")
    if not content: continue
    prompt = content["video_prompt"]
    img_b64, _ = load_reference_images_for_prompt(prompt)
    tid, err = submit_task(prompt, img_b64, payload_tag=f"batch_{evt['title']}")
    tasks.append({"title": evt["title"], "task_id": tid})
    time.sleep(3)

with open("/tmp/tasks.json", "w") as f:
    json.dump(tasks, f, ensure_ascii=False, indent=2)
```

### 4.4 下载 Seedance 视频

```python
from tutu_core.seedance_client import query_task, download_video
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

def dl(t):
    d = query_task(t["task_id"])
    if d.get("status") == "succeeded":
        url = d["content"]["video_url"]
        ok, info = download_video(url, Path(f"videos/{t['title']}.mp4"))
        return f"{'✅' if ok else '❌'} {t['title']}: {info}"
    return f"⏳ {t['title']}: {d.get('status')}"

with ThreadPoolExecutor(max_workers=4) as pool:
    for fut in as_completed([pool.submit(dl, t) for t in tasks]):
        print(fut.result(), flush=True)
```

`download_video` 已内置重试 3 次 + 180 秒超时 + 断点续传（已存在且大于 10KB 的跳过）。

### 4.5 Web App（包含完整 pipeline）

```
cd app/
python server.py
# → http://localhost:8000
```

FastAPI 服务启动后会后台自动轮询 Seedance 结果，定期下载完成的视频。

---

## 5. 输出产物

| 路径 | 内容 |
|------|------|
| `prompt生成系统/output/videos/placeholder_v1plus_*.mp4` | 最新 v1+ 占位符工作流视频 |
| `prompt生成系统/output/videos/01-20_*.mp4` | 历史 batch 种子视频（供 App 启动时 seed DB） |
| `prompt生成系统/output/preview_v1plus/preview_*.md` | 每个生成事件的 preview（LLM 原文 + final prompt + 图片表） |
| `prompt生成系统/output/submitted_prompts/{task_id}.txt` | submit_task 成功时自动归档的 prompt 原文 |

---

## 6. 故障排查

### 6.1 Gemini 429（配额耗尽）

- 现象：`429 Too Many Requests`
- 处理：
  1. 等待刷新（通常几小时到次日）
  2. 或修改 `.env` 的 `GEMINI_URL` 换模型（如 `gemini-2.5-flash`）
  3. 或设 `LLM_PROVIDER=ark` + 修 `tutu_core/config.py::ARK_LLM_MODEL` 为有效豆包 model id

### 6.2 Seedance 内容安全过滤

- 现象：`InputTextSensitiveContentDetected`
- 处理：调整 event summary 措辞，避开"弹飞"/"打砸"/"撞"等暴力倾向词

### 6.3 占位符未被替换

- 检查 `submit_task` 是否被调用（只有它会触发 `resolve_expression_placeholders`）
- 传 `img_b64` 必须是 list（单张图时不会触发注入/替换）

### 6.4 LLM 仍在开头写"图片1是..."

- 已被 `inject_image_declaration` 自动剥离并用规则重贴，不影响最终 prompt
- 但会稍微浪费 token；如 LLM 持续不听话，强化 system_prompt 警告

### 6.5 检测到说话/台词

- 现象：`quality_review` 报 `检测到说话/台词（禁止）`
- 原因：LLM 写了 `仿佛在说"..."` / `像在说："..."` 之类的
- 处理：已在 generation.py 加硬性规则，`generate_event_content` 内部会自动带反馈重试

---

## 附：核心文件索引

| 文件 | 职责 |
|------|------|
| `tutu_core/generation_router.py` | 版本分派（v1+/v2 路由） |
| `tutu_core/generation.py` | v1+ 生成逻辑 + 质量校验 |
| `tutu_core/generation_v2.py` | v2 生成逻辑（复用 v1 辅助函数） |
| `tutu_core/seedance_client.py` | 参考图加载 / 占位符解析 / 声明注入 / Seedance 提交 |
| `tutu_core/visual_style.py` | 视觉风格共用常量（风格+光线+构图） |
| `tutu_core/config.py` | 环境变量 / 路径 / 关键词库 |
| `tutu_core/validators.py` | Prompt 验证（字数/必须词/禁止词） |
| `prompt生成系统/ip-constitution.md` | IP 宪法（角色铁律） |
| `prompt生成系统/examples-library.md` | 类别范例（few-shot） |
