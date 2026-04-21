# -*- coding: utf-8 -*-
"""
内容生成核心逻辑 v2 — 基于示例优质 prompt 迭代的新版本

v2 相对 v1 的改进（从 example_prompts 学习的共性）：
  1. 固定视觉风格标签（日系写实摄影 + 大光圈浅景深 + 胶片颗粒感 + 低饱和暖色调）
  2. 光线/时段/光源精确化（午后柔和自然光 / 夜晚台灯暖黄）
  3. 比例参照数字化（与具体物品的尺寸关系）
  4. 运镜构图固化（水平中心对称构图 + 镜头固定平拍 + 低平视角）
  5. 15 秒 5-6 段分镜（替代 13 秒 4 段均匀分）
  6. 音效作为独立段落（"配乐/音效：" 块）
  7. 动作描写具象化（要求"短手扒住/一点一点"级别）
  8. 温馨治愈收尾语汇（"画面温柔定格" + "极轻极满足的嘟——"）
  9. 叠词密度要求（软塌塌/圆滚滚/极轻极细）

保留 v1 的所有能力（不动）：
  - 分类系统 / 范例注入 / 质量校验循环
  - 特写参考图 + 表情匹配参考图
  - 外貌铁律（嘴巴张开黑色、肢体末端圆形无手指、无尾巴/眉毛）
"""

import json
import logging

# 复用 v1 的所有辅助函数，不重新实现
from tutu_core.generation import (
    _load_cached,
    classify_event,
    _load_example_for_category,
    _get_category_guidance,
    quality_review,
)
from tutu_core.config import PERSONALITY_FILE
from tutu_core.llm_client import call_llm, extract_json

logger = logging.getLogger("tutu.generation_v2")


# ============================================================
# v2 视觉风格 — 常量抽至 visual_style.py（v1 也会复用）
# ============================================================

from tutu_core.visual_style import (
    VISUAL_STYLE_TOKENS, TIME_TO_LIGHT, time_to_light as _time_to_light,
)


# ============================================================
# v2 质量校验（额外检查 v2 独有的要素）
# ============================================================

_V2_STYLE_WORDS = ["日系", "写实", "浅景深", "颗粒感", "暖色调", "柔和", "胶片"]
_V2_COMPOSITION_WORDS = ["对称构图", "固定平拍", "低平视角", "镜头固定", "中心对称"]
_V2_ENDING_WORDS = ["画面温柔定格", "画面定格", "温柔定格", "静静照", "定格"]
_V2_REDUPLICATION = ["软塌塌", "圆滚滚", "毛茸茸", "暖洋洋", "极轻极", "一点一点", "慢慢", "duang", "啪叽"]


def quality_review_v2(prompt_text: str, category: str) -> tuple[bool, list[str]]:
    """v2 质量校验 — 在 v1 基础上追加风格/构图/收尾/叠词检查。"""
    passed_v1, issues = quality_review(prompt_text, category)

    # 视觉风格标签
    if not any(w in prompt_text for w in _V2_STYLE_WORDS):
        issues.append("缺少视觉风格标签（应含：日系写实/浅景深/胶片颗粒感等）")

    # 运镜构图
    if not any(w in prompt_text for w in _V2_COMPOSITION_WORDS):
        issues.append("缺少固化的运镜描述（应含：水平中心对称构图/镜头固定平拍/低平视角）")

    # 温馨收尾
    if not any(w in prompt_text for w in _V2_ENDING_WORDS):
        issues.append('缺少温馨治愈收尾（应有"画面温柔定格"或"暖光静静照着"类语汇）')

    # 叠词密度（至少 1 处；示例普遍 1-3 处）
    reduplication_count = sum(1 for w in _V2_REDUPLICATION if w in prompt_text)
    if reduplication_count < 1:
        issues.append(f"叠词/拟声词密度不足（{reduplication_count}处，建议≥1个如软塌塌/圆滚滚/极轻极细）")

    # 音效独立段落（寻找 "配乐/音效" 或 "音效：" 作为块标识）
    if "配乐/音效" not in prompt_text and prompt_text.count("音效：") < 2:
        issues.append("建议将音效作为独立段落：开头用“配乐/音效：”引出，按时间码列出")

    passed = len(issues) <= 1
    return passed, issues


# ============================================================
# 日程生成（与 v1 完全一致）
# ============================================================

def generate_schedule(
    date_str: str,
    weather: str = "",
    user_city: str = "",
    hot_signals: str = "",
    interactions: str = "",
    journal: str = "",
) -> list[dict]:
    """v2 的日程生成与 v1 完全一致（日程本身不需要风格化）。"""
    from tutu_core.generation import generate_schedule as v1_generate_schedule
    return v1_generate_schedule(date_str, weather, user_city, hot_signals, interactions, journal)


# ============================================================
# 视频内容生成（v2 重写 system_prompt）
# ============================================================

def generate_event_content(
    event: dict,
    date_str: str,
    interactions: str = "",
    max_attempts: int = 2,
) -> dict | None:
    """
    v2 版事件内容生成。

    关键差异（相对 v1）：
      - system_prompt 注入固定视觉风格 + 运镜构图 + 光线时段模板
      - 要求 15 秒 5-6 段分镜（起承转合结构）
      - 要求音效作为独立 "配乐/音效：" 段落
      - 要求具象化动作描写 + 叠词密度
      - 要求温馨治愈收尾

    保留 v1 不变的部分：
      - 分类 → 注入范例 + 类别指引
      - 外貌铁律（嘴巴黑色/圆手无指等）
      - 质量校验循环（但用 v2 版校验）
    """
    title = event.get("title", "")
    summary = event.get("summary", "")
    time_str = event.get("time", "12:00")

    category = classify_event(title, summary)
    logger.info(f"[v2] 事件 [{title}] 分类为: {category}")

    example = _load_example_for_category(category)
    guidance = _get_category_guidance(category)
    personality = _load_cached(PERSONALITY_FILE)
    light_desc = _time_to_light(time_str)

    system_prompt = f"""{personality}

你需要为秃秃的一个生活事件生成：
1. 视频prompt（给Seedance视频模型）
2. 心理活动文案（菇的碎碎念，30-60字）
3. 碎碎念时间线（2-3条短句）

{guidance}

【v2 视觉风格铁律】
本次视频必须落在统一的美学里，prompt 中要显式写出这些元素：
· 摄影风格：{VISUAL_STYLE_TOKENS}
· 光线：{light_desc}
· 构图：优先"水平中心对称构图" + "镜头固定平拍" + "低平视角贴近桌面高度"
· 比例：小蘑菇只有 4cm 高，必须给出它和周围物品的**相对尺寸参照**（如"杯子约是它身高的 1.5 倍"、"抱枕差不多和身体一样大"、"桌上的物品对它来说都是巨大的"）
· 场景虚化：背景物体用"浅景深虚化成柔和色块"

【v2 分镜节奏】
15 秒总时长，按"起-承-转-合"四幕组织，分成 5-6 个时间码段：
· 0-2s 或 0-3s：**起** — 静态环境建立 + 主角登场，慢而安静
· 2-6s 或 3-7s：**承** — 主线行动开始，1-2 个具体动作
· 6-10s 或 7-12s：**转** — 动作升级或意外发生（高潮瞬间）
· 10-15s 或 12-15s：**合** — 情感收尾 + 画面温柔定格

【v2 动作描写要求】
描写必须**具象到身体动态层级**，不能一笔带过：
· 反例："秃秃爬上杯子" → 正例："短手扒住杯子把手往上爬，短腿蹬着杯壁爬了两下滑了一下，再使劲一蹬翻上杯沿"
· 反例："秃秃摔倒了" → 正例："'啪叽'一声掉在座垫上，果冻身体在座垫上弹了两下才停住，仰面朝天四肢摊开"
· 至少 2 处叠词或身体物理描写：软塌塌 / 圆滚滚 / 毛茸茸 / 极轻极细 / 一点一点 / 慢慢 / duang / 啪叽 / 噗叽 / 唰

【v2 音效段落】
视频 prompt 末尾必须单独用"配乐/音效："引出一个段落，按时间码列出每段音效：
配乐/音效：
0-Xs ... Xs-Ys ... Ys-Zs ...
一段写一句，每句描写 1-3 个具体音效（环境音 + 动作音 + 情绪音）。

【v2 温馨治愈收尾】
最后 2-3 秒必须是情感定格，至少包含以下之一：
· "画面温柔定格"
· "极轻极满足的'嘟——'"
· "暖光静静照着..."
· "微风/鸟鸣/环境音贯穿"

【视频prompt硬性规则（与 v1 一致，请严格遵守）】
· **不要**在开头写"图片1是... 图片2是..."等图片声明（系统会规则注入，你写的会被覆盖）
· **直接从"摄影风格："这一行开始写**
· 固定编号：图片1=角色 / 图片2=肢体末端 / 图片3=张嘴 / 图片4=屁股 / 图片5=全身（可直接引用）
· **表情图用占位符**（系统会替换为图片6+）：`{{happy}}` 开心 / `{{cry}}` 委屈哭泣 / `{{shy}}` 害羞 / `{{angry}}` 生气奶凶 / `{{laugh}}` 大笑
· 正文引用示例：
  - 手部：`参考图片2的肢体末端形态`
  - 张嘴：`参考图片3嘴内深色的圆洞`
  - 强情绪：`眯眼笑腮帮鼓鼓（参考{{happy}}表情图）`、`眼泪在眼眶里（参考{{cry}}情绪图）`
· 总字数控制在 600-900 字（v2 比 v1 略长，因为加了风格/光线/音效段落）
· 不要有吃东西的画面

【禁止说话（硬性铁律）】
秃秃不能说人话，不能有任何台词或独白。
· 严禁出现：`秃秃说"..."` / `仿佛在说"..."` / `无声地说"..."` / `心里想"..."` / `意思是"..."`
  **任何引号包裹的完整人类语句都不允许**，即使加了"无声地""仿佛""好像"等缓冲词
· 只能用拟声词（拟声词可以出现）：嘟 / 嘟嘟 / 嘟～ / 嘟！ / 嘟—— / 呼噜 / 哼哧 / 哈欠（啊——呜——）/
  啪叽 / duang / 噗 / 咔 / 沙沙 / 噔噔 等
· 情绪只通过表情+肢体动作+拟声词传达
· 反例：`仿佛无声地说："看，我搞定了。"` ❌
· 正例：`带着一点骄傲望向镜头，发出一声满足的"嘟～"` ✅

【角色外貌铁律（v1 继承，描述时严格遵守用词）】
· **不要使用"手"或"小手"这两个词**。改称"肢体末端"或"圆球状的小短肢"；动作用"扒住/按上去/抱住/扶着"
· 肢体末端是圆滚滚的肉球状（像棉花糖/小汤圆/毛球），整体是一个圆球，没有任何指头分叉，没有爪状结构
· 嘴巴张开时里面是纯黑色的小圆洞（无牙齿、无舌头），平时只是一个小弧线
· 身体：没有尾巴、没有眉毛

【视频 prompt 末尾必须写】
"只要音效，不要背景音乐，不要字幕。角色特征：小蘑菇的嘴巴张开时里面是黑色的小圆洞（无牙齿无舌头），身体两侧伸出的肢体末端是圆滚滚的肉球状/棉花糖状（整体一个圆球，无分开的指头，无爪状结构），没有眉毛，没有尾巴。" """

    example_block = ""
    if example:
        example_block = f"""
---以下是同类别的标杆范例，参考它的质感描写密度、表情节拍精度、音效设计水准---

{example}

---范例结束，请达到或超过这个质量水准---"""

    feedback_note = ""

    for attempt in range(max_attempts):
        user_prompt = f"""{example_block}

日期：{date_str}
事件时间：{event['time']}
事件标题：{title}
事件描述：{summary}
事件类别：{category}
是否与用户相关：{event.get('user_related', False)}
触发来源：{event.get('triggered_by', 'daily')}

用户最近互动：
{interactions}
{feedback_note}
请严格按以下JSON格式输出：
```json
{{
  "video_prompt": "摄影风格：...。光线：...。构图：...。比例：...。背景物体用浅景深虚化成柔和色块。(5-6段分镜 + 配乐/音效段 + 温馨收尾)（不要写图片1/图片2声明，直接从摄影风格开始）",
  "inner_voice": "菇的碎碎念文案......",
  "thoughts": [{{"time": "{event['time']}", "text": "短句1"}}, {{"time": "{event['time']}", "text": "短句2"}}]
}}
```"""

        raw = call_llm(system_prompt, user_prompt, max_tokens=3000, use_cache=False)
        if not raw:
            continue

        try:
            content = extract_json(raw)
        except (json.JSONDecodeError, ValueError):
            continue

        prompt_text = content.get("video_prompt", "")
        if not prompt_text.strip():
            continue

        # v2 质量校验（final prompt 会在 submit 时被注入图片1声明）
        passed, issues = quality_review_v2(prompt_text, category)

        if passed:
            voice = content.get("inner_voice", "")
            if not content.get("thoughts") and voice:
                content["thoughts"] = [{"time": event["time"], "text": voice[:60]}]
            content["category"] = category
            content["generation_version"] = "v2"
            if attempt > 0:
                logger.info(f"[v2][{title}] 第{attempt+1}次生成通过质量校验")
            return content

        if attempt < max_attempts - 1:
            feedback_note = "\n\n⚠️ 上一次生成的质量问题（请在这次修正）：\n" + "\n".join(f"- {i}" for i in issues)
            logger.info(f"[v2][{title}] 质量校验未通过({len(issues)}个问题)，重试中...")
        else:
            logger.warning(f"[v2][{title}] 质量校验仍未通过: {issues}")
            voice = content.get("inner_voice", "")
            if not content.get("thoughts") and voice:
                content["thoughts"] = [{"time": event["time"], "text": voice[:60]}]
            content["category"] = category
            content["generation_version"] = "v2"
            return content

    return None
