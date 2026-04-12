# -*- coding: utf-8 -*-
"""
Batch 04 + 闹钟重做 Seedance 提交脚本（已重构：使用 tutu_core）
"""
import sys
import json
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tutu_core.config import OUTPUT_DIR
from tutu_core.markdown_parser import extract_prompts
from tutu_core.validators import validate_prompt
from tutu_core.seedance_client import load_reference_image, submit_task

PROMPT_FILE = OUTPUT_DIR / "batch04_秃秃的一天v2_待确认.md"

# 闹钟重做的纯prompt（软化情绪版）
ALARM_PROMPT = """图片1是小蘑菇角色形象参考。微缩场景，小蘑菇只有4cm高，构图中近景，角色不要太大不要超过画面三分之一。场景：清晨卧室床头柜上，一个圆形复古金属小闹钟放在柜子右侧，闹钟和蘑菇角色差不多高。窗帘缝隙透进来淡淡晨光，整体安静柔和。微距浅景深，背景虚化可见床铺。角色没有手指和牙齿。

0-3s：蘑菇角色蜷在闹钟旁边熟睡，小脸贴着金属面，身体一起一伏。突然闹钟铃响了，蘑菇角色被震得弹离闹钟，啪叽一声轻轻落在柜面上弹了一下，蘑菇帽被震歪了。它迷迷糊糊睁开眼，茫然地眨了眨。音效：极轻呼吸声和钟摆滴答声，铃声响起，蘑菇角色"嘟！"小小惊讶，落地啪叽声。

3-7s：蘑菇角色揉了揉眼睛，歪头看着还在响的闹钟，表情困惑。它慢慢走到闹钟旁边，两只小短手试探地碰了碰闹钟——闹钟还在震，把它的小短手震得duangduang抖。它缩回手甩了甩，犹豫了一下，又伸手去摸闹钟背面找按钮。音效：闹钟铃铃铃持续，小短手碰到金属的叮声，手被震的duangduang声，蘑菇角色困惑的"嘟？"。

7-10s：蘑菇角色找到按钮，两只小短手按上去——咔哒，铃声停了。安静了。它趴在闹钟旁边松了口气，蘑菇帽歪着没扶。伸出小短手轻轻拍了拍闹钟顶部，像在说"好了好了别响了"。音效：咔哒声后安静，松气"嘟……哈"，拍闹钟叮叮两声。

10-13s：蘑菇角色打了个大哈欠，眼皮又开始打架。它靠着闹钟慢慢坐下来，往闹钟身上蹭了蹭找舒服的位置。抬头看了一眼镜头，眼睛半睁半闭的表情很困很委屈，像在说"人家还没睡够"。然后脑袋一歪靠在闹钟上又睡着了，呼吸声恢复均匀。画面定格。音效：哈欠"嘟啊～"，蹭闹钟毛绒蹭金属沙沙声，最后呼吸声"呼——嘟……"。只要音效，不要背景音乐，不要字幕。注意：小蘑菇没有牙齿、没有舌头、没有眉毛。"""


def main():
    # 加载图片
    print("加载参考图片...")
    img_b64 = load_reference_image()

    # 提取batch04 prompt
    prompts = extract_prompts(str(PROMPT_FILE))
    print(f"提取到 {len(prompts)} 条batch04 prompt")

    # 加入闹钟重做
    prompts.insert(0, {"num": 2, "title": "闹钟重做(软化版)", "text": ALARM_PROMPT.strip()})
    print(f"加入闹钟重做，共 {len(prompts)} 条\n")

    # 验证
    print("=" * 60)
    all_ok = True
    for p in prompts:
        passed, issues = validate_prompt(p["text"])
        status = "✅" if passed else "❌"
        if not passed:
            all_ok = False
        print(f"{status} #{p['num']:02d} {p['title']} ({len(p['text'])}字)")
        for issue in issues:
            print(f"   {issue}")
    print("=" * 60)

    if not all_ok:
        print("\n存在错误，退出")
        sys.exit(1)

    # 2并发提交
    print(f"\n提交到 Seedance API（2并发）\n")
    results = []
    for i in range(0, len(prompts), 2):
        batch = prompts[i:i+2]
        for p in batch:
            task_id, error = submit_task(p["text"], img_b64, payload_tag=f"b04_{p['num']:02d}")
            if task_id:
                print(f"  ✅ #{p['num']:02d} {p['title']} -> {task_id}")
                results.append({"num": p["num"], "title": p["title"], "task_id": task_id})
            else:
                print(f"  ❌ #{p['num']:02d} {p['title']} -> {error}")
                results.append({"num": p["num"], "title": p["title"], "task_id": None, "error": error})

    print(f"\n{'=' * 60}")
    success = sum(1 for r in results if r.get("task_id"))
    print(f"{success}/{len(results)} 提交成功")
    with open("/tmp/batch04_tasks.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"任务ID已保存到 /tmp/batch04_tasks.json")


if __name__ == "__main__":
    main()
