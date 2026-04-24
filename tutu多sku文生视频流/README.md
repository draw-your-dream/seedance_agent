# TUTU Multi-SKU 5s 文生视频流

本目录是 TUTU 文生视频 pipeline 的 **5 秒单镜头、多 SKU 随机、三图参考** 版本。**所有 Python 代码都在本目录内，不依赖其它 pipeline 的脚本**。

主线：

```text
Phase A：生成 A1 context (Gemini 调用)
        │
        ▼
Phase B：每条 context 随机选 1/7 SKU，解析 3 张参考图路径，派生标题（确定性，无 LLM）
        │
        ▼
Phase C：按 Phase B 的 blueprint 调 Gemini，生成 5 秒单镜头 T2V prompt
        │
        ▼
Phase D：prompt + 3 张参考图（SKU 四视图 + 手脚 + 嘴巴）→ Ark Seedance
        │
        ▼
Ark Seedance 文生视频（duration=5, ratio=9:16）
```

## 为什么拆成 A/B/C/D

- **Phase A** 和 **Phase C** 都调 Gemini，职责明确
- **Phase B** 夹在中间做确定性准备：挑 SKU、查路径、写标题。这些不需要 LLM，硬把它塞进 Phase C 会让"生成 prompt"这一步同时做两件事
- 拆开的收益：
  - 想换 system prompt？只重跑 Phase C，Phase B 的 blueprint 不动，SKU 分配稳定可比
  - 想换 SKU 随机种子？只重跑 Phase B，Phase A 的 context 不动
  - 三阶段之间通过 jsonl 串联，任一阶段都可单独重跑、幂等覆盖

## 和 agent_fast 的区别

| 维度 | agent_fast | 本目录 |
|------|-----------|--------|
| 阶段 | A / C / D | **A / B / C / D** |
| 视频时长 | 15 秒 | **5 秒** |
| 镜头 | 允许自然跟随，但对分镜语言弱约束 | **强约束：一个连续镜头，绝不切镜，允许合理运镜** |
| 动作节奏写法 | 按时间段 `0-3s / 3-7s ...` | **只用"首先/接着/随后/然后/最后"等顺序词，禁用时间码** |
| 尺度控制 | 用"不写 4cm / 不写比例" 否定词 | **正向描述：场景里写周围物体和蘑菇TUTU 的具体比例对比（借鉴 xiangyu）** |
| SKU | 固定单一 reference.png | **每条 prompt 随机从 7 种 SKU 中选一种** |
| 参考图 | 1 张 | **3 张：SKU 四视图 + 手脚参考图 + 嘴巴参考图** |
| SKU/路径/标题准备 | 混在 Phase C 里 | **独立的 Phase B 做确定性准备** |
| Prompt 写作格式 | 自由长段落 | **参考 seedance_prompt_agent：首段图片关系 + 风格 + 镜头 + 场景 + 配乐/音效 + 约束** |

## 目录结构

```
tutu多sku文生视频流/
├── README.md                                   # 本文件
├── 逻辑说明.md                                  # 设计取舍和尺度控制推理
├── 完整流程图.md                                # 四阶段流程图 + 文件 I/O 地图 (mermaid)
├── 所有系统prompt总览.md                         # 所有送给 LLM 的 prompt 的镜像文档
├── phase_a.py                                   # Phase A：A1 context 生成（Gemini）
├── phase_a_system_prompt.md                    # Phase A 主 system prompt（含 {personality}/{constitution} 占位）
├── phase_a_pool_subprompts.md                  # Phase A 七池子子 prompt（daily/weather/background/lifestyle/action/mood/guardrail）
├── phase_b_multi_sku_blueprints.py             # Phase B：随机 SKU + 参考图路径 + 标题（无 LLM）
├── phase_c_multi_sku_t2v_prompts.py            # Phase C：blueprint → T2V prompt（Gemini）
├── phase_c_multi_sku_t2v_system_prompt.md      # Phase C 系统提示词（5s/无切镜/顺序词/尺度控制/3图引用）
├── phase_d_multi_sku_t2v_videos.py             # Phase D：prompt + 3 张参考图 → Ark Seedance
├── ip_data/                                     # TUTU IP 基础资料（本目录持有副本）
│   ├── daily_signals.json
│   ├── personality.md
│   └── ip-constitution.md
├── sku/                                         # 参考图（本目录持有副本）
│   ├── 1.png ~ 7.png                            # 七种蘑菇TUTU SKU 的四视图
│   ├── hand_foot.jpg                            # 手和脚（肢体末端）参考图
│   └── mouth.jpg                                # 嘴巴参考图
└── outputs/                                     # 运行时生成
    ├── phase_a/<timestamp>/
    │   ├── phase_a_contexts.jsonl
    │   └── manifest.json
    ├── multi_sku_blueprints/
    │   ├── phase_b_multi_sku_blueprints.jsonl
    │   └── phase_b_multi_sku_blueprints.md
    ├── multi_sku_t2v_prompts/
    │   ├── phase_c_multi_sku_t2v_prompts.jsonl
    │   └── phase_c_multi_sku_t2v_prompts.md
    └── multi_sku_t2v_videos/
        ├── multi_sku_t2v_tasks.jsonl
        ├── payloads/00001.json
        └── videos/00001_sku3_xxx.mp4
```

## System prompt 清单

> **想看完整流程图？** → 看 [`完整流程图.md`](完整流程图.md)（含四阶段数据流、system prompt 注入、文件 I/O 地图的 mermaid 图）
>
> **想一次看完所有 prompt？** → 看 [`所有系统prompt总览.md`](所有系统prompt总览.md)（镜像/索引文档）
>
> 以下各份 md 是**源头文件**，Python 代码从这些加载（不从总览加载）。改规则请改这些源头 md。

本 pipeline 里所有**送给 LLM 的 system prompt 都抽成 md 文件**，Python 代码负责加载而不是硬编码：

| 文件 | 用途 | 加载方 |
|------|------|--------|
| `phase_a_system_prompt.md` | Phase A 主系统提示词（Gemini 用来把 blueprint 融合成完整 A1 context）。`{personality}` 和 `{constitution}` 两个占位符在运行时替换成 `ip_data/personality.md` 和 `ip_data/ip-constitution.md` 的完整内容 | `phase_a.py` → `build_context_generation_prompts()` |
| `phase_a_pool_subprompts.md` | Phase A 七个池子的子 system prompt：`daily`（按 slot 分 5 条）、`weather`（按天气分 5 条 + 1 条 default）、`background`、`lifestyle`、`action`、`mood`、`guardrail`。以 `## pool` / `### key` 结构解析，作为每条 context 的 `reference_hints` 附进 blueprint | `phase_a.py` → `parse_pool_subprompts()` |
| `phase_c_multi_sku_t2v_system_prompt.md` | Phase C 主系统提示词（整份就是系统 prompt，无占位符）。Gemini 用它把 Phase B blueprint 改写成 5 秒 T2V prompt | `phase_c_multi_sku_t2v_prompts.py` |

**Phase B 和 Phase D 不调 LLM**，所以没有 system prompt。

修改规则时直接改对应 md 即可，不需要动 Python 代码。

## 资源依赖

本目录**完全自包含**：Python 代码 + IP 数据 + 参考图 全部在本目录下，不依赖任何外部路径。

**参考图**（本目录 `sku/` 下的副本，Phase B / Phase D 读取）：

- `sku/1.png` ~ `sku/7.png`：七种蘑菇TUTU SKU 的四视图（源自 `../秃秃sku四视图/`）
- `sku/hand_foot.jpg`：手和脚（肢体末端）参考图
- `sku/mouth.jpg`：嘴巴参考图

**IP 基础资料**（本目录 `ip_data/` 下的副本，`phase_a.py` 读取）：

- `ip_data/daily_signals.json`（源自 `../prompt生成系统/v2/`）
- `ip_data/personality.md`（源自 `../prompt生成系统/v2/`）
- `ip_data/ip-constitution.md`（源自 `../prompt生成系统/`）

如果上游 IP 定义或 SKU 素材有更新，需要手动 `cp` 覆盖 `ip_data/` 或 `sku/` 下的对应文件。

## 运行命令

Windows 下统一用 `F:\workspace\tutu内容\_tools\python311\python.exe` 作为 Python 解释器。

### 1. Phase A：生成 A1 context

```powershell
& 'F:\workspace\tutu内容\_tools\python311\python.exe' 'F:\workspace\tutu内容\tutu多sku文生视频流\phase_a.py' --count 50
```

默认输出：

```
F:\workspace\tutu内容\tutu多sku文生视频流\outputs\phase_a\<timestamp>\
  phase_a_contexts.jsonl
  manifest.json
```

常用参数：

- `--count N`：生成多少条 context，默认 50
- `--run-label STR`：这批素材的标签，默认 `multi_sku_batch`
- `--batch-size N`：每次调 Gemini 处理多少条，默认 10
- `--seed N`：模拟输入种子，默认 `20260424`

### 2. Phase B：随机 SKU + 参考图路径 + 标题

```powershell
& 'F:\workspace\tutu内容\_tools\python311\python.exe' 'F:\workspace\tutu内容\tutu多sku文生视频流\phase_b_multi_sku_blueprints.py'
```

**默认行为**：自动找 `outputs/phase_a/` 下**最近修改**的 `phase_a_contexts.jsonl`，为每条 context 随机抽 1/7 SKU。无 LLM 调用，很快就跑完。

默认输出：

```
F:\workspace\tutu内容\tutu多sku文生视频流\outputs\multi_sku_blueprints\
  phase_b_multi_sku_blueprints.jsonl
  phase_b_multi_sku_blueprints.md
```

常用参数：

- `--limit N`：最多处理多少条，默认 0（全部）
- `--seed N`：SKU 随机种子，可复现，默认 `20260424`
- `--contexts-jsonl PATH`：显式指定 A1 context 文件

### 3. Phase C：生成 5 秒 T2V prompt

```powershell
& 'F:\workspace\tutu内容\_tools\python311\python.exe' 'F:\workspace\tutu内容\tutu多sku文生视频流\phase_c_multi_sku_t2v_prompts.py' --limit 3
```

**默认行为**：自动找 `outputs/multi_sku_blueprints/` 下最近的 `phase_b_multi_sku_blueprints.jsonl`，按 blueprint 里的 `sku_index` 和 `context` 调 Gemini。

默认输出：

```
F:\workspace\tutu内容\tutu多sku文生视频流\outputs\multi_sku_t2v_prompts\
  phase_c_multi_sku_t2v_prompts.jsonl
  phase_c_multi_sku_t2v_prompts.md
```

常用参数：

- `--limit N`：最多处理多少条
- `--force`：重新覆盖已有结果
- `--blueprints-jsonl PATH`：显式指定 Phase B 产物文件

### 4. Phase D：dry-run 看 payload

```powershell
& 'F:\workspace\tutu内容\_tools\python311\python.exe' 'F:\workspace\tutu内容\tutu多sku文生视频流\phase_d_multi_sku_t2v_videos.py' --limit 1
```

payload 会写到 `outputs/multi_sku_t2v_videos/payloads/00001.json`。手动检查：3 张 reference_image 是否齐全；prompt 是否以"图片1"开头；duration 是否是 5。

### 5. Phase D：实际提交 + 等待 + 下载

```powershell
$env:ARK_API_KEY='你的 Ark API key'
& 'F:\workspace\tutu内容\_tools\python311\python.exe' 'F:\workspace\tutu内容\tutu多sku文生视频流\phase_d_multi_sku_t2v_videos.py' `
  --limit 3 `
  --execute `
  --wait `
  --download `
  --force
```

默认参数：

- `model`: `doubao-seedance-2-0-260128`
- `ratio`: `9:16`
- `duration`: `5`
- `generate_audio`: `true`
- `watermark`: `false`

## Payload 结构

Phase D 给 Seedance 的 payload：

```json
{
  "model": "doubao-seedance-2-0-260128",
  "content": [
    {"type": "text", "text": "图片1是蘑菇TUTU的四视图……"},
    {"type": "image_url", "image_url": {"url": "<SKU i.png>"}, "role": "reference_image"},
    {"type": "image_url", "image_url": {"url": "<hand_foot.jpg>"}, "role": "reference_image"},
    {"type": "image_url", "image_url": {"url": "<mouth.jpg>"}, "role": "reference_image"}
  ],
  "generate_audio": true,
  "ratio": "9:16",
  "duration": 5,
  "watermark": false
}
```

注意：Ark 官方示例里 `image_url` 通常要用公网 URL。当前脚本默认会把本地三张图片都转成 data URL 试用。如果你的 Ark 项目不支持 data URL，请换成公网 URL 参数：

```powershell
--hand-foot-image-url 'https://.../hand_foot.jpg'
--mouth-image-url 'https://.../mouth.jpg'
```

SKU 图片的本地路径写在 Phase C 的 JSONL 里（从 Phase B 带过来），默认转 data URL；如果要用公网 URL 承载 SKU，请在 Phase B 后手动修改 JSONL 里的 `sku_image_path`。

## Prompt 格式（seedance_prompt_agent 风格）

每条 prompt 的结构：

```
图片1是蘑菇TUTU的四视图，严格参考图片1的蘑菇TUTU形象、毛绒质感、身体比例和帽子颜色。
图片2是蘑菇TUTU的手和脚参考图，严格参考图片2里肢体末端的毛绒圆球形态和粉色小肉垫颜色……
图片3是蘑菇TUTU的嘴巴参考图，嘴巴张开时的嘴形、嘴内颜色和粉红色嘴唇轮廓严格参考图片3。

风格：……

镜头：……

场景：首先…接着…随后…然后…最后…（动作节奏用顺序词串，禁用时间码；内含至少两处蘑菇TUTU 与周围物体的具体比例对比）

配乐/音效：……

约束：……
```

校验：Phase C 每生成一条 prompt 都会跑 `validate_prompt_shape`，必须满足：

- 首段以"图片1是蘑菇TUTU的四视图"开头
- 包含 `图片1` `图片2` `图片3` 三个引用
- 包含 `风格：` `镜头：` `场景：` `配乐/音效：` `约束：` 五个段落标签

并且 `sanitize_prompt` 会硬兜底删掉：

- `4cm`、`微缩尺度`、`不要超过画面 X` 等抽象尺度词
- `分镜`、`切到 POV`、`镜头切换` 等切镜语言
- `0-1.5s`、`第3秒`、`0-2秒` 等时间码

任一校验失败会自动重试（最多 2 次）。

## 推荐测试方式

```powershell
# 全链路小跑一遍
phase_a.py --count 6
phase_b_multi_sku_blueprints.py
phase_c_multi_sku_t2v_prompts.py --limit 3
phase_d_multi_sku_t2v_videos.py --limit 1                                    # dry-run
phase_d_multi_sku_t2v_videos.py --limit 1 --execute --wait --download       # 真跑一条
```

观察四件事：

1. 看 `phase_b_multi_sku_blueprints.md`：SKU 分布是否合理？每条 blueprint 的三张参考图路径是否存在？
2. 看 `phase_c_multi_sku_t2v_prompts.md`：prompt 是否真的提到三张图片？尺度比例是否写具体了？有没有时间码？
3. 看 `payloads/00001.json`：3 张图片是否都在；duration 是否是 5。
4. 看下载的 mp4：是 5 秒；单镜头没切；蘑菇TUTU 和周围物体比例看起来像微缩角色；手脚是圆球（图片2 生效）；张嘴是参考嘴巴图的嘴形（图片3 生效）。

详细设计取舍参见 `逻辑说明.md`。
