# -*- coding: utf-8 -*-
"""
内容生成核心逻辑 — 日程生成 + 视频内容生成（含类别分类、范例注入、质量校验）

从 scheduler.py 和 life_engine.py 的重复代码中提取统一实现。
接入 prompt 体系的全部资源：examples-library / category-templates / quality-checklist
"""

import json
import logging
from pathlib import Path

from tutu_core.config import (
    PERSONALITY_FILE, IP_CONSTITUTION_FILE, PROMPT_SYSTEM_DIR,
)
from tutu_core.llm_client import call_llm, extract_json

logger = logging.getLogger("tutu.generation")


# ============================================================
# 文件缓存
# ============================================================

_file_cache: dict[str, tuple[float, str]] = {}


def _load_cached(path) -> str:
    """带 mtime 缓存的文件读取。"""
    path = Path(path)
    key = str(path)
    if not path.exists():
        return ""
    mtime = path.stat().st_mtime
    if key in _file_cache and _file_cache[key][0] == mtime:
        return _file_cache[key][1]
    text = path.read_text(encoding="utf-8")
    _file_cache[key] = (mtime, text)
    return text


# ============================================================
# 类别系统：分类 + 范例 + 模板
# ============================================================

# 类别关键词映射（用于快速匹配，避免额外 LLM 调用）
CATEGORY_KEYWORDS = {
    "美食制作": ["做", "制作", "烹", "煮", "烤", "蒸", "炸", "煎", "切", "揉面", "裱花", "调饮", "冲泡"],
    "美食吃播": ["吃", "品尝", "尝", "偷吃", "大快朵颐", "试吃", "啃", "舔"],
    "日常生活": ["赖床", "起床", "洗澡", "睡觉", "打扫", "晒太阳", "发呆", "等", "躲", "藏"],
    "第一次认识": ["第一次", "认识", "发现", "好奇", "研究", "打量", "什么东西"],
    "户外主题": ["户外", "公园", "花", "树", "雨", "雪", "海", "湖", "山", "风", "江南", "樱花", "散步",
                 "晒太阳", "秋千", "水坑", "草地", "沙滩", "星星", "月亮", "野餐", "蝴蝶", "瓢虫"],
    "职业扮演": ["扮演", "工作", "店员", "师", "员", "摊", "卖", "开店", "打工"],
}

# 类别 → 范例映射标识（对应 examples-library.md 中的范例编号）
CATEGORY_EXAMPLE_KEYS = {
    "美食制作": "A",
    "美食吃播": "B",
    "日常生活": "C",
    "第一次认识": "D",
    "户外主题": "E",
    "职业扮演": "F",
}

# 互动 beat 速查（从 category-templates.md 提取）
INTERACTION_BEATS = [
    "两只小手把东西推向镜头方向，脸涨红用力但很认真",
    '指了指东西又指了指镜头，歪头眨眼，好像在说"这个给你"',
    '转一圈向镜头展示成果，叉腰满意地"嘟嘟！"',
    "望向镜头，表情委屈/困惑，像在求助",
    "突然发现镜头在看，赶紧躲到东西后面假装没事",
    '扑向镜头直到怼脸大特写，满足地"嘟～"',
    "朝镜头挥动小手，开心打招呼",
    "靠着物品安心地待着，抬头看向镜头露出温暖的笑",
]


def classify_event(title: str, summary: str = "") -> str:
    """基于关键词快速分类事件到6大类别。"""
    text = f"{title} {summary}".lower()
    scores = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        scores[cat] = sum(1 for kw in keywords if kw in text)
    best = max(scores, key=scores.get)
    if scores[best] > 0:
        return best
    # 默认归类为日常生活
    return "日常生活"


def _load_example_for_category(category: str) -> str:
    """加载对应类别的标杆范例（few-shot）。"""
    examples_text = _load_cached(PROMPT_SYSTEM_DIR / "examples-library.md")
    if not examples_text:
        return ""
    key = CATEGORY_EXAMPLE_KEYS.get(category, "C")
    # 提取对应范例段落（## 范例{key}：... 到下一个 ## 范例 或文件结尾）
    marker = f"## 范例{key}："
    start = examples_text.find(marker)
    if start < 0:
        return ""
    # 找下一个范例的开头或文件末尾
    next_marker = examples_text.find("\n## 范例", start + len(marker))
    if next_marker < 0:
        next_marker = examples_text.find("\n## 使用说明", start)
    if next_marker < 0:
        next_marker = len(examples_text)
    example = examples_text[start:next_marker].strip()
    # 限制长度避免 context 过长
    if len(example) > 1500:
        example = example[:1500] + "\n...（范例截断）"
    return example


def _get_category_guidance(category: str) -> str:
    """根据类别生成针对性的创作指引。"""
    guidance_map = {
        "美食制作": """本事件是美食制作类。要求：
· 情绪弧线：准备食材 → 认真制作 → 成品展示 → 反转收尾（偷吃/搞砸/分享给镜头）
· 食物质感描写要极致：逐层颜色（至少3种颜色）、状态变化（切开/融化/冒泡）
· 音效要逐动作指定：倒液体咕噜声、搅拌沙沙声、切割咔声、冒泡声等
· 收尾互动beat：把成品推向镜头/偷吃后若无其事递给镜头""",

        "美食吃播": """本事件是美食吃播类。要求：
· 情绪弧线：发现食物 → 好奇打量 → 试探第一口 → 表情炸裂 → 大快朵颐
· 食物描写要逐层：从上到下/从外到内，每层颜色+质感
· 表情要逐拍递进：第一口眼睛一亮→第二口嚼更快→第三口腮帮子鼓到最大
· 腮帮子 duangduang 弹动是核心画面
· 音效用 ASMR 级：咀嚼声、吸溜声、腮帮子弹动声""",

        "日常生活": """本事件是日常生活类。要求：
· 情绪弧线：发现/遇到人类物品 → 好奇互动 → 体型差异导致意外 → 温馨收尾
· 核心卖点是4cm体型带来的物理喜剧：东西太大/太重/够不到
· 身体材质要体现：duang弹跳、啪叽落地、像液体一样软
· 至少1个比例反差笑点
· 音效要体现身体物理特性：duang声、啵声、噗声""",

        "第一次认识": """本事件是"第一次认识X"类。要求：
· 三幕结构：好奇接近 → 用蘑菇逻辑误解/误用物品 → 搞笑或惊吓结果
· 重点写秃秃不理解人类物品的可爱误解
· 从秃秃视角描写物品：对它来说像什么、有多大
· 每个镜头有明确的情绪进展和音效设计
· 收尾要有与镜头的情感互动（兴奋/害怕/求助/得意）""",

        "户外主题": """本事件是户外/主题场景类。要求：
· 情绪弧线：氛围沉浸 → 缓慢探索 → 自然互动 → 诗意定格
· 场景描写要诗意：自然环境、光线、色彩、氛围至少3句
· 与自然元素互动要细腻：触碰水面/接住花瓣/感受微风
· 身体细节：毛绒边缘的光晕、帽子上落了花瓣、微风吹动
· 节奏慢而沉浸，收尾定格在某个美好瞬间""",

        "职业扮演": """本事件是职业扮演类。要求：
· 情绪弧线：上岗准备 → 认真工作 → 体型导致职业闹剧 → 反转收尾
· 秃秃穿着迷你工服/拿着巨大工具，形成比例反差
· 认真工作时要表现"专业感"（虽然很搞笑）
· 搞笑反转：偷吃产品/搞砸工作/理直气壮继续
· 收尾把不完整的成果若无其事递给镜头""",
    }
    return guidance_map.get(category, guidance_map["日常生活"])


# ============================================================
# 质量校验
# ============================================================

_QUALITY_CRITERIA = [
    ("时间码", r'\d+-\d+s', "缺少时间码分段（应有0-3s/3-7s/7-10s/10-13s）"),
    ("音效", "音效", "缺少逐动作音效描写（不是笼统一句，要每段都有具体音效）"),
    ("表情", None, "缺少逐拍表情变化（不是只写一个'开心'，要每个动作节拍有表情描写）"),
    ("互动beat", None, "结尾缺少互动beat（望镜头/递东西/挥手/眨眼）"),
    ("构图", "不要太大", "缺少构图约束（角色不要太大/不超过画面三分之一）"),
]

# 表情相关关键词
_EXPRESSION_WORDS = ["眼睛", "腮帮", "嘴", "帽子", "表情", "眯", "鼓", "歪头", "愣", "笑", "红"]
# 互动 beat 关键词
_INTERACTION_WORDS = ["镜头", "看向", "递向", "推向", "挥手", "眨眼", "定格", "望向"]


def quality_review(prompt_text: str, category: str) -> tuple[bool, list[str]]:
    """
    快速质量审查（本地，不调用 LLM）。
    返回 (passed, issues)。issues 是需要改进的具体建议列表。

    阈值经过 examples-library.md 中7个标杆范例校准。
    """
    import re
    issues = []

    # 时间码检查 — 匹配多种写法：0-3s / 0-2s：/ 镜1（0-3s）/ 第一段
    timecodes = re.findall(r'\d+-\d+s|镜\d|镜头\d|第[一二三四五六]段', prompt_text)
    if len(timecodes) < 2:
        issues.append("分段不足：只有{}段标记，建议至少3-4段分镜".format(len(timecodes)))

    # 音效密度检查 — 包含拟声词和音效描述动词
    sound_words = [
        "音效", "声", "duang", "啪", "咔", "嘎", "噗", "叮", "咕", "滋",
        "嘟", "boing", "啵", "沙沙", "咚", "嗖", "吱", "哗", "噔",
        "清脆", "沉闷", "轻响", "碎响", "叮当",
    ]
    sound_count = sum(prompt_text.count(w) for w in sound_words)
    if sound_count < 2:
        issues.append("音效描写不够（只有{}处，建议每段都有具体音效/拟声词）".format(sound_count))

    # 表情/动作密度检查 — 扩大关键词范围
    expr_words = [
        "眼睛", "腮帮", "嘴", "帽子", "表情", "眯", "鼓", "歪头", "愣", "笑",
        "红", "脸蛋", "瞪", "撅", "缩", "抱", "叉腰", "竖", "捂",
        "点头", "摇头", "哈欠", "眨眼", "嘴角",
    ]
    expr_count = sum(prompt_text.count(w) for w in expr_words)
    if expr_count < 2:
        issues.append("表情/动作描写太少（只有{}处，建议有表情或肢体细节）".format(expr_count))

    # 互动 beat 检查 — 扩大匹配范围
    interaction_words = [
        "镜头", "看向", "递向", "推向", "挥手", "眨眼", "定格", "望向",
        "蹭了蹭", "安心", "满足", "对着", "看着", "靠着",
    ]
    has_interaction = any(w in prompt_text for w in interaction_words)
    if not has_interaction:
        issues.append("缺少互动/情感收尾：结尾应有情感表达或与镜头互动")

    # 构图约束检查 — 匹配更多变体
    composition_words = ["不要太大", "三分之一", "不要太小", "中景", "中近景", "对称构图"]
    has_composition = any(w in prompt_text for w in composition_words)
    if not has_composition:
        issues.append("缺少构图约束：应注明景别或角色大小限制")

    # 类别特定检查
    if category in ("美食制作", "美食吃播"):
        color_words = ["红", "白", "粉", "黄", "金", "绿", "紫", "褐", "焦", "淡", "深", "浅"]
        color_count = sum(1 for w in color_words if w in prompt_text)
        if color_count < 2:
            issues.append("食物颜色描写不足（只有{}种颜色词，建议至少3层颜色）".format(color_count))

    if category == "美食吃播":
        if "腮帮" not in prompt_text:
            issues.append("吃播类缺少腮帮子描写（duangduang弹动是核心画面）")

    # 字数检查 — 标杆范例最短约200字（摊煎饼的单段），放宽到300
    if len(prompt_text) < 300:
        issues.append("prompt偏短（{}字），建议至少300字".format(len(prompt_text)))

    passed = len(issues) <= 1  # 允许最多1个非关键问题
    return passed, issues


# ============================================================
# 日程生成
# ============================================================

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
8. 不要有吃东西的画面
9. 事件类型尽量多样化：日常生活、第一次认识X、户外、互动向等混搭"""

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


# ============================================================
# 视频内容生成（含分类 + 范例注入 + 质量校验循环）
# ============================================================

def generate_event_content(
    event: dict,
    date_str: str,
    interactions: str = "",
    max_attempts: int = 2,
) -> dict | None:
    """
    为单个事件生成视频 prompt + 心理活动。

    流程：分类 → 注入范例+指引 → 生成 → 质量校验 → 不通过则带反馈重试一次

    返回: {"video_prompt", "inner_voice", "thoughts", "category"} 或 None
    """
    title = event.get("title", "")
    summary = event.get("summary", "")

    # Step 1: 分类
    category = classify_event(title, summary)
    logger.info(f"事件 [{title}] 分类为: {category}")

    # Step 2: 加载范例和指引
    example = _load_example_for_category(category)
    guidance = _get_category_guidance(category)
    personality = _load_cached(PERSONALITY_FILE)

    # Step 3: 构建增强 prompt
    system_prompt = f"""{personality}

你需要为秃秃的一个生活事件生成：
1. 视频prompt（给Seedance视频模型）
2. 心理活动文案（菇的碎碎念，30-60字）
3. 碎碎念时间线（2-3条短句）

{guidance}

视频prompt硬性规则：
· 必须以"图片1是小蘑菇角色形象参考。"开头
· 微缩场景，小蘑菇只有4cm高，构图中近景，角色不要太大不要超过画面三分之一
· 包含4段时间码：0-3s / 3-7s / 7-10s / 10-13s
· 每段都要有具体的音效描写（不是笼统一句"有音效"，是逐动作指定）
· 每段都要有表情/动作细节（不是"秃秃很开心"，是"眼睛眯起来腮帮子鼓鼓的"）
· 结尾2-3秒有互动beat（望镜头/递东西/眨眼/定格）
· 末尾必须写："只要音效，不要背景音乐，不要字幕。角色特征：小蘑菇的嘴巴张开时里面是黑色的小圆洞（无牙齿无舌头），手部是圆滚滚的小肉球形状像棉花糖/小汤圆（整体一个圆形，无分开的指头），没有眉毛，没有尾巴。"
· 总字数控制在500-700字
· 不要有吃东西的画面
· 角色外貌铁律（非常重要）：
  - 手：**没有手指、没有爪子**。手是圆滚滚的小肉球，像棉花糖/小汤圆的形状，整体一个圆，绝对不要画成爪子或五指分开的手
  - 嘴：没有牙齿、没有舌头。嘴巴张开时里面是纯黑色的（像个小黑洞）
  - 身体：没有尾巴、没有眉毛"""

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
  "video_prompt": "图片1是小蘑菇角色形象参考。......",
  "inner_voice": "菇的碎碎念文案......",
  "thoughts": [{{"time": "{event['time']}", "text": "短句1"}}, {{"time": "{event['time']}", "text": "短句2"}}]
}}
```"""

        raw = call_llm(system_prompt, user_prompt, max_tokens=2500, use_cache=False)
        if not raw:
            continue

        try:
            content = extract_json(raw)
        except (json.JSONDecodeError, ValueError):
            continue

        prompt_text = content.get("video_prompt", "")
        if not prompt_text.startswith("图片1"):
            continue

        # Step 4: 质量校验
        passed, issues = quality_review(prompt_text, category)

        if passed:
            # 补全 thoughts
            voice = content.get("inner_voice", "")
            if not content.get("thoughts") and voice:
                content["thoughts"] = [{"time": event["time"], "text": voice[:60]}]
            content["category"] = category
            if attempt > 0:
                logger.info(f"[{title}] 第{attempt+1}次生成通过质量校验")
            return content

        # 第一次不通过 → 带反馈重试
        if attempt < max_attempts - 1:
            feedback_note = "\n\n⚠️ 上一次生成的质量问题（请在这次修正）：\n" + "\n".join(f"- {i}" for i in issues)
            logger.info(f"[{title}] 质量校验未通过({len(issues)}个问题)，重试中...")
        else:
            # 最后一次也没通过，仍然返回（总比没有好）
            logger.warning(f"[{title}] 质量校验仍未通过: {issues}")
            voice = content.get("inner_voice", "")
            if not content.get("thoughts") and voice:
                content["thoughts"] = [{"time": event["time"], "text": voice[:60]}]
            content["category"] = category
            return content

    return None
