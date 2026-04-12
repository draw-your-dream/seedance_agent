# -*- coding: utf-8 -*-
"""
Batch 03 Seedance 提交脚本（已重构：使用 tutu_core）
"""
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tutu_core.config import OUTPUT_DIR, VIDEO_DIR
from tutu_core.markdown_parser import extract_prompts
from tutu_core.validators import validate_prompt
from tutu_core.seedance_client import load_reference_image, submit_task

import json

PROMPT_FILE = OUTPUT_DIR / "batch03_精简版prompt_待确认.md"


def main():
    # Step 1: 提取prompt
    prompts = extract_prompts(str(PROMPT_FILE))
    print(f"提取到 {len(prompts)} 条prompt\n")

    if not prompts:
        print("❌ 没有提取到任何prompt，检查md文件格式")
        sys.exit(1)

    # Step 2: 逐条验证
    print("=" * 60)
    print("PROMPT 验证")
    print("=" * 60)
    all_ok = True
    for p in prompts:
        passed, issues = validate_prompt(p["text"])
        status = "✅" if passed else "❌"
        if not passed:
            all_ok = False
        print(f"\n{status} #{p['num']:02d} | {p['title']} ({len(p['text'])}字)")
        print(f"   开头: {p['text'][:80]}...")
        print(f"   结尾: ...{p['text'][-50:]}")
        for issue in issues:
            print(f"   {issue}")

    print("\n" + "=" * 60)

    if not all_ok:
        print("⚠️ 存在问题项，请检查后重新运行")

    # Step 3: 加载图片
    print("\n加载参考图片...")
    img_b64 = load_reference_image()

    # Step 4: 2并发提交
    print(f"\n{'=' * 60}")
    print(f"提交到 Seedance API（2并发）")
    print(f"{'=' * 60}\n")

    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    results = []

    for i in range(0, len(prompts), 2):
        batch = prompts[i:i+2]
        for p in batch:
            task_id, error = submit_task(p["text"], img_b64, payload_tag=f"b03_{p['num']:02d}")
            if task_id:
                print(f"  ✅ #{p['num']:02d} {p['title']} -> {task_id}")
                results.append({"num": p["num"], "title": p["title"], "task_id": task_id})
            else:
                print(f"  ❌ #{p['num']:02d} {p['title']} -> {error}")
                results.append({"num": p["num"], "title": p["title"], "task_id": None, "error": error})

    # 保存结果
    print(f"\n{'=' * 60}")
    success = sum(1 for r in results if r.get("task_id"))
    print(f"{success}/{len(results)} 提交成功")

    with open("/tmp/batch03_tasks.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"任务ID已保存到 /tmp/batch03_tasks.json")


if __name__ == "__main__":
    main()
