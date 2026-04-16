# TUTU Fast Prompt 总览

fast 版目标：跳过文生图 prompt 和首帧图，直接从 A1 context 生成 Seedance 文生视频 prompt。

```text
Phase A context -> Fast Phase C T2V prompt -> Fast Phase D Seedance video
```

## Phase A：A1 Context

脚本：

[phase_a.py](/f:/workspace/tutu内容/agent_fast/phase_a.py)

产物：

- `phase_a_contexts.jsonl`
- `manifest.json`

fast 版 Phase A 不再生成文生图 prompt。只要 `phase_a_contexts.jsonl`。

## Fast Phase C：Seedance 文生视频 Prompt

脚本：

[phase_c_seedance_t2v_prompts.py](/f:/workspace/tutu内容/agent_fast/phase_c_seedance_t2v_prompts.py)

系统提示词：

[phase_c_seedance_t2v_system_prompt.md](/f:/workspace/tutu内容/agent_fast/phase_c_seedance_t2v_system_prompt.md)

输入：

- A1 context

不输入：

- 不输入任何文生图中间层
- 不输入首帧图
- 不读取 Replicate 结果

输出：

- `phase_c_seedance_t2v_prompts.jsonl`
- `phase_c_seedance_t2v_prompts.md`

prompt 必须以这句开头：

```text
以参考图中的蘑菇TUTU作为唯一角色参考，保持角色外观特征一致
```

写法重点：

- 按 15 秒视频写。
- 蘑菇TUTU是唯一主体。
- 场景、道具、空间关系从 A1 context 推导。
- 必须延续 `action_theme`，但要扩展成完整连续动作。
- 不能写“以输入图片为第一帧”。
- 不能写“保持首帧构图”。
- 不要假装存在首帧图。
- 要有清楚动作、互动、表情或眼神变化。
- 不要写 `4cm`、`微缩`、`微小体量`、`微小感`、`比例`、`尺度`。

## Fast Phase D：Ark Seedance 文生视频 Payload

脚本：

[phase_d_seedance_t2v_videos.py](/f:/workspace/tutu内容/agent_fast/phase_d_seedance_t2v_videos.py)

输入：

- `phase_c_seedance_t2v_prompts.jsonl`
- 角色参考图

不输入：

- 不输入首帧图 URL
- 不输入 Replicate predictions

Payload 示例：

```json
{
  "model": "doubao-seedance-2-0-260128",
  "content": [
    {
      "type": "text",
      "text": "以参考图中的蘑菇TUTU作为唯一角色参考，保持角色外观特征一致，..."
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "角色参考图的 URL 或 data URL"
      },
      "role": "reference_image"
    }
  ],
  "generate_audio": true,
  "ratio": "9:16",
  "duration": 15,
  "watermark": false
}
```

## 适用场景

fast 版适合快速验证：

- 只靠 A1 context 能不能产出足够完整的视频 prompt。
- 跳过首帧图后，场景和动作是否仍然可控。
- 文生视频是否比原版图生视频更快、更便宜。
