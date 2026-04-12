#!/usr/bin/env python3
"""
秃秃IP动画Prompt批量生成器
支持 Claude API 和 OpenAI API，自动组合三层内容、批量生成、质量校验。

使用方式:
    # 生成内容日历（10条）
    python batch_generator.py calendar --count 10

    # 从日历文件展开完整prompt
    python batch_generator.py expand --calendar calendar.json --output prompts/

    # 单条生成
    python batch_generator.py single --category 美食吃播 --theme "秃秃第一次吃芒果"

    # 质量校验
    python batch_generator.py validate --input prompts/

环境变量:
    ANTHROPIC_API_KEY  - Claude API密钥
    OPENAI_API_KEY     - OpenAI API密钥（备选）
    LLM_PROVIDER       - 选择 "claude"(默认) 或 "openai"
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tutu_core.validators import quick_validate
from tutu_core.llm_client import call_llm as core_call_llm
from tutu_core.generation import quality_review, classify_event

# ============================================================
# 配置
# ============================================================

SCRIPT_DIR = Path(__file__).parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"
CALENDAR_FILENAME = "calendar.json"

# 类别分布（默认比例，共30条）
DEFAULT_DISTRIBUTION = {
    "美食制作": 7,
    "美食吃播": 6,
    "日常生活": 5,
    "第一次认识X": 4,
    "户外主题": 3,
    "职业扮演": 2,
    "互动向": 3,
}

# 已有主题（去重用）
EXISTING_THEMES = [
    "黄帽角色PV", "粉帽角色PV", "白帽角色PV", "抓娃娃", "泡泡鸡",
    "第一次认识书", "第一次认识耳机", "第一次认识墨镜", "神奇棉花糖",
    "电灯泡许愿", "跟小猫看鱼", "大吃冰激凌", "偷吃猫粮", "小番茄三吃",
    "超人pose", "偷吃蛋糕", "森林朋友圈", "紫玉椰奶糕", "周末日常",
    "液体蘑菇", "摊煎饼", "方便面编制工", "牛脆脆", "土豆波纹",
    "消防员", "春日樱花", "唱歌", "草莓牛奶", "春日野餐", "打扫卫生",
    "裱花师", "罐子蛋糕", "三文鱼", "四宫格", "飞扑", "菇下江南",
    "体重秤", "近大远小", "行李箱", "翻书", "键盘", "沙发",
    "扫地机器人", "抽风", "冰箱睡觉", "草莓牛乳",
]

# ============================================================
# 文件加载
# ============================================================

def load_file(filename: str) -> str:
    """加载prompt生成系统目录下的md文件"""
    path = SCRIPT_DIR / filename
    if not path.exists():
        print(f"[警告] 文件不存在: {path}")
        return ""
    return path.read_text(encoding="utf-8")


def load_ip_constitution() -> str:
    return load_file("ip-constitution.md")


def load_examples_library() -> str:
    return load_file("examples-library.md")


def load_category_templates() -> str:
    return load_file("category-templates.md")


def load_quality_checklist() -> str:
    return load_file("quality-checklist.md")


def get_example_for_category(category: str) -> str:
    """根据类别返回对应的标杆范例"""
    examples = load_examples_library()
    category_example_map = {
        "美食制作": "范例A",
        "美食吃播": "范例B",
        "日常生活": "范例C",
        "第一次认识X": "范例D",
        "户外主题": "范例E",
        "职业扮演": "范例F",
        "互动向": "范例G",
    }
    marker = category_example_map.get(category, "范例A")
    # 提取对应范例段落
    sections = examples.split("## " + marker)
    if len(sections) > 1:
        content = sections[1]
        next_section = content.find("\n## 范例")
        if next_section > 0:
            content = content[:next_section]
        return f"## {marker}{content}"
    return ""


# ============================================================
# LLM 调用封装
# ============================================================

def call_llm(system_prompt: str, user_prompt: str, max_tokens: int = 8000) -> str:
    """统一 LLM 调用，委托给 tutu_core.llm_client（支持 Gemini/ARK/Claude/OpenAI）"""
    result = core_call_llm(system_prompt, user_prompt, max_tokens=max_tokens, use_cache=False)
    if result is None:
        raise RuntimeError("LLM 调用失败，请检查 API 配置")
    return result


# ============================================================
# 核心System Prompt构建
# ============================================================

def build_system_prompt() -> str:
    """构建完整的System Prompt（IP宪法+规则）"""
    constitution = load_ip_constitution()

    return f"""你是秃秃IP动画短片prompt创作专家。你的任务是为AI视频生成模型撰写高质量的动画脚本prompt。

{constitution}

# 7条质量红线（每条都必须满足）

1. **逐秒时间码**：必须有分秒或分镜时间标注（每2-4秒一个beat）
2. **质感可视化**：食物/物品描写必须具体到颜色+层次+质感
3. **表情逐拍**：表情变化精确到每个动作节拍
4. **音效逐动作**：每个动作对应具体音效描写
5. **比例反差**：至少1个因4cm身高产生的尺度对比笑点
6. **情绪弧线**：明确的起承转合，至少3个情绪转折点
7. **构图具体**：景别+景深+对称性+前中后景都要写明

# 互动规则
- 纯互动向prompt：核心剧情围绕与观众（镜头）的互动
- 非互动向prompt：结尾最后2-3秒加入轻互动beat（望镜头/递东西/挥手/眨眼）

# 绝对禁止
不出现手指、牙齿、舌头、爪子、字幕；角色不说人话只说"嘟"系列；角色不要太大"""


# ============================================================
# 功能一：生成内容日历
# ============================================================

def generate_calendar(count: int, distribution: dict = None) -> list:
    """生成内容日历"""
    if distribution is None:
        # 按比例缩放到目标数量
        total_default = sum(DEFAULT_DISTRIBUTION.values())
        distribution = {}
        remaining = count
        for cat, default_n in DEFAULT_DISTRIBUTION.items():
            n = round(count * default_n / total_default)
            distribution[cat] = n
            remaining -= n
        # 把余数分配给最多的类别
        if remaining != 0:
            max_cat = max(distribution, key=distribution.get)
            distribution[max_cat] += remaining

    system_prompt = build_system_prompt()
    existing_list = "、".join(EXISTING_THEMES)
    dist_text = "\n".join(f"- {cat}：{n}个" for cat, n in distribution.items())

    user_prompt = f"""请为秃秃IP生成{count}个视频主题的内容日历。

类别分布：
{dist_text}

排除已有主题（不要重复）：{existing_list}

请严格按以下JSON格式输出，不要输出其他内容：
```json
[
  {{
    "id": 1,
    "category": "类别名",
    "theme": "主题名（简短有趣）",
    "concept": "核心创意点（一句话描述画面）",
    "emotion_arc": "情绪线（用→连接4个情绪词）",
    "interaction_type": "纯互动/轻互动收尾",
    "duration": 12
  }}
]
```

要求：
1. 主题名要有画面感和趣味性
2. 核心创意点要具体到一个画面/动作，不要泛泛而谈
3. 情绪线要有转折，不能全是"好奇→开心→满足→开心"
4. 纯互动向的prompt占总数约30%
5. 确保类别内不重复核心创意机制"""

    print(f"[日历] 正在生成{count}条内容日历...")
    response = call_llm(system_prompt, user_prompt)

    # 解析JSON
    try:
        # 提取JSON部分
        json_start = response.find("[")
        json_end = response.rfind("]") + 1
        if json_start >= 0 and json_end > json_start:
            calendar = json.loads(response[json_start:json_end])
        else:
            raise ValueError("未找到JSON数组")
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[错误] JSON解析失败: {e}")
        print(f"[原始输出]\n{response}")
        return []

    print(f"[日历] 成功生成{len(calendar)}条主题")
    return calendar


# ============================================================
# 功能二：展开完整Prompt
# ============================================================

def expand_prompt(entry: dict) -> str:
    """将日历条目展开为完整prompt"""
    category = entry.get("category", "日常生活")
    theme = entry.get("theme", "未命名")
    concept = entry.get("concept", "")
    emotion_arc = entry.get("emotion_arc", "")
    interaction_type = entry.get("interaction_type", "轻互动收尾")
    duration = entry.get("duration", 12)

    system_prompt = build_system_prompt()

    # 获取对应范例
    example = get_example_for_category(category)
    example_section = ""
    if example:
        example_section = f"""
以下是同类别的优秀范例，请参考其质量密度和写法风格：

{example}
"""

    user_prompt = f"""{example_section}

请将以下内容日历条目展开为完整的秃秃动画prompt：

类别：{category}
主题：{theme}
核心创意：{concept}
情绪线：{emotion_arc}
互动类型：{interaction_type}
时长：{duration}秒

请按以下格式输出完整prompt：

---
【角色形象参考】...
【画面总述】...（含氛围基调、音效/配乐说明、整体运镜风格）
【场景置景】...（微缩世界设定，人类比例参照物）
【分镜时间码】
0-Xs: ...
X-Ys: ...
...
【音效设计】...（逐动作音效列表）
【绝对禁止】不出现手指、牙齿、舌头、爪子、字幕；角色不说人话只说"嘟"；角色不要太大
---

严格遵守IP宪法和7条质量红线。"""

    print(f"  [展开] {theme}...")
    result = call_llm(system_prompt, user_prompt)
    return result


# ============================================================
# 功能三：质量校验
# ============================================================

# quick_validate 已移至 tutu_core.validators，通过顶部 import 引入


def llm_validate(prompt_text: str) -> str:
    """使用LLM进行深度质量校验"""
    checklist = load_quality_checklist()
    system_prompt = "你是秃秃IP动画prompt的质量审核专家。请严格按照校验清单逐项检查。"
    user_prompt = f"""{checklist}

请检查以下prompt：

---
{prompt_text}
---

按照校验清单输出结果表格和修正建议。"""

    return call_llm(system_prompt, user_prompt, max_tokens=4000)


# ============================================================
# 功能四：多样性审计
# ============================================================

def diversity_audit(prompts: list[dict]) -> list[str]:
    """检查一批prompt的多样性"""
    warnings = []

    # 检查连续场景重复
    for i in range(len(prompts) - 2):
        cats = [prompts[j].get("category", "") for j in range(i, i + 3)]
        if len(set(cats)) == 1:
            warnings.append(f"条目{i+1}-{i+3}连续3个同类别「{cats[0]}」")

    # 检查情绪线雷同
    emotion_arcs = [p.get("emotion_arc", "") for p in prompts]
    from collections import Counter
    arc_counts = Counter(emotion_arcs)
    for arc, count in arc_counts.items():
        if count > 2 and arc:
            warnings.append(f"情绪线「{arc}」重复{count}次")

    # 检查主题与已有重复
    for p in prompts:
        theme = p.get("theme", "")
        for existing in EXISTING_THEMES:
            if existing in theme or theme in existing:
                warnings.append(f"主题「{theme}」与已有「{existing}」可能重复")

    return warnings


# ============================================================
# 主流程
# ============================================================

def cmd_calendar(args):
    """生成内容日历"""
    calendar = generate_calendar(args.count)
    if not calendar:
        return

    # 多样性审计
    warnings = diversity_audit(calendar)
    if warnings:
        print("\n[多样性警告]")
        for w in warnings:
            print(f"  ⚠ {w}")

    # 保存
    output_dir = Path(args.output) if args.output else DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / CALENDAR_FILENAME

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(calendar, f, ensure_ascii=False, indent=2)

    print(f"\n[保存] 日历已保存到: {output_path}")

    # 打印预览
    print("\n--- 内容日历预览 ---")
    for entry in calendar:
        print(f"  {entry['id']:2d}. [{entry['category']}] {entry['theme']} — {entry['concept']}")


def cmd_expand(args):
    """从日历展开完整prompt"""
    calendar_path = Path(args.calendar)
    if not calendar_path.exists():
        print(f"[错误] 日历文件不存在: {calendar_path}")
        return

    with open(calendar_path, "r", encoding="utf-8") as f:
        calendar = json.load(f)

    output_dir = Path(args.output) if args.output else DEFAULT_OUTPUT_DIR / "prompts"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 并发展开所有prompt（3并发，替代原来的逐条串行）
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _expand_one(entry):
        idx = entry.get("id", 0)
        theme = entry.get("theme", f"prompt_{idx}")
        prompt_text = expand_prompt(entry)
        return idx, theme, entry, prompt_text

    expanded = [None] * len(calendar)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_expand_one, e): i for i, e in enumerate(calendar)}
        for future in as_completed(futures):
            i = futures[future]
            try:
                expanded[i] = future.result()
            except Exception as e:
                entry = calendar[i]
                print(f"  ❌ [{entry.get('id', i)}] 展开失败: {e}")
                expanded[i] = (entry.get("id", i), entry.get("theme", "?"), entry, "")

    results = []
    for idx, theme, entry, prompt_text in expanded:
        if not prompt_text:
            continue

        # 质量校验（使用 generation 模块的增强版）
        cat = classify_event(theme, entry.get("concept", ""))
        qr_passed, qr_issues = quality_review(prompt_text, cat)
        # 同时跑旧的 quick_validate 保持向后兼容
        validation = quick_validate(prompt_text)
        all_issues = qr_issues + [i for i in validation.get("issues", []) if i not in qr_issues]
        combined_passed = qr_passed and validation["passed"]
        status = "✅" if combined_passed else f"⚠({len(all_issues)}项)"
        print(f"  [{idx:2d}] {theme} [{cat}] — {status}")
        if not combined_passed:
            for issue in all_issues:
                print(f"       ❌ {issue}")
        validation["passed"] = combined_passed
        validation["issues"] = all_issues

        # 保存单个prompt
        safe_name = theme.replace("/", "_").replace(" ", "_")
        prompt_path = output_dir / f"{idx:02d}_{safe_name}.md"
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(f"# {theme}\n\n")
            f.write(f"类别: {entry.get('category', '')}\n")
            f.write(f"情绪线: {entry.get('emotion_arc', '')}\n")
            f.write(f"互动类型: {entry.get('interaction_type', '')}\n\n")
            f.write("---\n\n")
            f.write(prompt_text)

        results.append({
            "id": idx,
            "theme": theme,
            "file": str(prompt_path),
            "validation": validation,
        })

    # 保存结果摘要
    summary_path = output_dir / "_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    passed = sum(1 for r in results if r["validation"]["passed"])
    print(f"\n[完成] 共展开{len(results)}个prompt，{passed}个通过快速校验")
    print(f"[保存] 输出目录: {output_dir}")


def cmd_single(args):
    """单条生成"""
    entry = {
        "id": 1,
        "category": args.category,
        "theme": args.theme,
        "concept": args.concept or "",
        "emotion_arc": args.emotion or "好奇→探索→惊喜→满足",
        "interaction_type": args.interaction or "轻互动收尾",
        "duration": args.duration or 12,
    }

    prompt_text = expand_prompt(entry)

    # 校验
    validation = quick_validate(prompt_text)
    if not validation["passed"]:
        print("\n[校验警告]")
        for issue in validation["issues"]:
            print(f"  ⚠ {issue}")

    # 输出
    print("\n" + "=" * 60)
    print(prompt_text)
    print("=" * 60)

    # 可选保存
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(prompt_text)
        print(f"\n[保存] 已保存到: {output_path}")


def cmd_validate(args):
    """批量校验"""
    input_dir = Path(args.input)
    if not input_dir.exists():
        print(f"[错误] 目录不存在: {input_dir}")
        return

    md_files = sorted(input_dir.glob("*.md"))
    if not md_files:
        print(f"[提示] 目录中没有找到.md文件: {input_dir}")
        return

    print(f"[校验] 找到{len(md_files)}个prompt文件\n")

    all_passed = 0
    for fp in md_files:
        if fp.name.startswith("_"):
            continue
        content = fp.read_text(encoding="utf-8")
        validation = quick_validate(content)
        status = "✅" if validation["passed"] else "❌"
        print(f"  {status} {fp.name} (得分: {validation['score']}/5)")
        if not validation["passed"]:
            for issue in validation["issues"]:
                print(f"     → {issue}")
        else:
            all_passed += 1

    print(f"\n[结果] {all_passed}/{len(md_files)} 通过快速校验")

    if args.deep:
        print("\n[深度校验] 使用LLM逐个深度检查...")
        for fp in md_files[:3]:  # 只深度检查前3个
            if fp.name.startswith("_"):
                continue
            content = fp.read_text(encoding="utf-8")
            print(f"\n--- 深度校验: {fp.name} ---")
            result = llm_validate(content)
            print(result)
            time.sleep(1)


# ============================================================
# CLI入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="秃秃IP动画Prompt批量生成器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python batch_generator.py calendar --count 10
  python batch_generator.py expand --calendar output/calendar.json
  python batch_generator.py single --category 美食吃播 --theme "秃秃吃芒果"
  python batch_generator.py validate --input output/prompts/
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # calendar
    p_cal = subparsers.add_parser("calendar", help="生成内容日历")
    p_cal.add_argument("--count", type=int, default=30, help="生成条数（默认30）")
    p_cal.add_argument("--output", type=str, help="输出目录")

    # expand
    p_exp = subparsers.add_parser("expand", help="从日历展开完整prompt")
    p_exp.add_argument("--calendar", type=str, required=True, help="日历JSON文件路径")
    p_exp.add_argument("--output", type=str, help="输出目录")

    # single
    p_single = subparsers.add_parser("single", help="单条生成")
    p_single.add_argument("--category", type=str, required=True, help="类别")
    p_single.add_argument("--theme", type=str, required=True, help="主题名")
    p_single.add_argument("--concept", type=str, help="核心创意点")
    p_single.add_argument("--emotion", type=str, help="情绪线")
    p_single.add_argument("--interaction", type=str, help="互动类型")
    p_single.add_argument("--duration", type=int, help="时长（秒）")
    p_single.add_argument("--output", type=str, help="输出文件路径")

    # validate
    p_val = subparsers.add_parser("validate", help="质量校验")
    p_val.add_argument("--input", type=str, required=True, help="prompt文件目录")
    p_val.add_argument("--deep", action="store_true", help="启用LLM深度校验（前3个）")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    commands = {
        "calendar": cmd_calendar,
        "expand": cmd_expand,
        "single": cmd_single,
        "validate": cmd_validate,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
