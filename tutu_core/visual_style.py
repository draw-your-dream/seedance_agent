# -*- coding: utf-8 -*-
"""
视觉风格共用常量 — v1/v2 都从这里 import，避免循环依赖。
"""

# 统一的摄影美学 token（日系写实 + 大光圈浅景深 + 胶片颗粒 + 低饱和暖色调）
VISUAL_STYLE_TOKENS = (
    "日系写实摄影风格，大光圈浅景深，低饱和暖色调，"
    "画面有真实胶片的颗粒感和柔和高光溢出，微缩世界写实风格"
)

# 按时段的光线模板
TIME_TO_LIGHT = {
    "早晨": "清晨柔和自然光从窗户斜照进来，色温偏暖，光斑柔软",
    "上午": "上午明亮柔和的自然光，色温中性偏暖，画面通透明亮",
    "中午": "正午自然光均匀洒在场景里，低饱和暖色调",
    "下午": "午后柔和自然光，阳光暖洋洋的，树影斑驳",
    "傍晚": "傍晚橘黄色夕阳光，色温明显偏暖，光线有方向感",
    "夜晚": "夜晚室内只有台灯发出暖黄色柔光照亮小一片区域，其余部分暗暗的，深夜独处氛围",
}


def time_to_light(time_str: str) -> str:
    """根据 HH:MM 时间返回对应的光线描述。"""
    try:
        hour = int(time_str.split(":")[0])
    except (ValueError, IndexError):
        return TIME_TO_LIGHT["下午"]
    if hour < 9:
        return TIME_TO_LIGHT["早晨"]
    elif hour < 12:
        return TIME_TO_LIGHT["上午"]
    elif hour < 14:
        return TIME_TO_LIGHT["中午"]
    elif hour < 18:
        return TIME_TO_LIGHT["下午"]
    elif hour < 20:
        return TIME_TO_LIGHT["傍晚"]
    else:
        return TIME_TO_LIGHT["夜晚"]


# 构图 + 比例 + 背景虚化描述（v1/v2 共用）
COMPOSITION_HINT = (
    "构图优先「水平中心对称构图 + 镜头固定平拍 + 低平视角贴近桌面/地面高度」；"
    "小蘑菇只有 4cm 高，必须给出它和周围物品的**相对尺寸参照**"
    "（如\"杯子约是它身高的 1.5 倍\"、\"抱枕差不多和身体一样大\"、\"桌上的物品对它来说都是巨大的\"）；"
    "背景物体用浅景深虚化成柔和色块"
)
