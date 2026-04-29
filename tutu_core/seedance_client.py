# -*- coding: utf-8 -*-
"""Seedance视频生成API客户端 — 统一提交/查询/下载逻辑"""

import json
import base64
import logging
import re
from pathlib import Path

import httpx

# 共享客户端：连接池复用
_http_client = httpx.Client(timeout=120, follow_redirects=True)

from tutu_core.config import (
    get_ark_api_key, SEEDANCE_API_URL, SEEDANCE_MODEL,
    SEEDANCE_DURATION, SEEDANCE_RATIO,
    REF_IMAGE, REF_IMAGE_ORIGINAL,
    REF_HAND_CLOSEUP, REF_MOUTH_SIDE, REF_BACK, REF_FULL_BODY,
    REF_EXPRESSION_FILES, EXPRESSION_KEYWORDS,
    REF_SCENE_FILES,
    REQUIRED_PREFIX, PROMPT_MIN_LENGTH,
    PROMPT_ARCHIVE_DIR,
)

logger = logging.getLogger("tutu.seedance")


def _archive_prompt(task_id: str, prompt_text: str, payload_tag: str = ""):
    """提交成功后将 prompt 文本按 task_id 落盘，便于后续审阅。"""
    try:
        PROMPT_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        fname = f"{task_id}.txt"
        with open(PROMPT_ARCHIVE_DIR / fname, "w", encoding="utf-8") as f:
            if payload_tag:
                f.write(f"# payload_tag: {payload_tag}\n# task_id: {task_id}\n# length: {len(prompt_text)}\n\n")
            f.write(prompt_text)
    except Exception as e:
        logger.warning(f"归档 prompt 失败（非致命）: {e}")


def load_reference_image(path: Path = None) -> str:
    """加载单张参考图片为base64字符串。默认优先用压缩版，fallback 到原图。"""
    if path is not None:
        img_path = path
    elif REF_IMAGE.exists():
        img_path = REF_IMAGE
    else:
        img_path = REF_IMAGE_ORIGINAL
    if not img_path.exists():
        raise FileNotFoundError(f"参考图片不存在: {img_path}")
    with open(img_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    logger.info(f"参考图片已加载: {img_path} ({len(img_b64)} chars)")
    return img_b64


def load_all_reference_images() -> list[str]:
    """加载主参考图 + 固定的4张特写（手部/张嘴/屁股/全身）。

    返回 base64 列表，第一张是主参考图（图片1），后续是特写补充。
    只加载存在的文件，不会因缺失特写而报错。
    """
    images = [load_reference_image()]  # 主参考图必须存在

    extras = [
        (REF_HAND_CLOSEUP, "手部特写"),
        (REF_MOUTH_SIDE, "张嘴侧面"),
        (REF_BACK, "屁股特写"),
        (REF_FULL_BODY, "全身正面"),
    ]
    for path, label in extras:
        if path.exists():
            with open(path, "rb") as f:
                images.append(base64.b64encode(f.read()).decode())
            logger.info(f"额外参考图已加载: {label} ({path.name})")
    return images


def match_expressions(prompt_text: str) -> list[str]:
    """根据 prompt 文本匹配出应该附加的表情参考图 key 列表。

    匹配两种来源，合并去重：
    1. EXPRESSION_KEYWORDS 关键词（如"眯眼笑"、"委屈"）
    2. 显式占位符（如 `{happy}` / `{cry}`）— LLM 主动标注的引用点

    返回按 EXPRESSION_KEYWORDS 声明顺序排列的 key 列表。
    """
    matched = set()
    for expr_key, keywords in EXPRESSION_KEYWORDS.items():
        if any(kw in prompt_text for kw in keywords):
            matched.add(expr_key)
    for expr_key in EXPRESSION_KEYWORDS:
        if f"{{{expr_key}}}" in prompt_text:
            matched.add(expr_key)
    # 保持 EXPRESSION_KEYWORDS 的声明顺序
    return [k for k in EXPRESSION_KEYWORDS if k in matched]


_EXPR_BASE_INDEX = 6  # 图片 1-5 是固定图，表情图从图片6 开始

# 场景占位符 {scene:KEY} —— 与表情占位符 {happy} 平行的机制，
# 但只走显式占位符（不做关键词扫描），避免家居词如"沙发/床"误触发现有 collection prompt
_SCENE_PLACEHOLDER_RE = re.compile(r'\{scene:([a-z_]+)\}')


def match_scenes(prompt_text: str) -> list[str]:
    """从 prompt 里扫 `{scene:KEY}` 占位符。返回按 REF_SCENE_FILES 声明顺序的 key 列表。"""
    found = set(_SCENE_PLACEHOLDER_RE.findall(prompt_text))
    return [k for k in REF_SCENE_FILES if k in found]


def resolve_expression_placeholders(
    prompt_text: str,
    matched_order: list[str],
    matched_scenes: list[str] | None = None,
    scene_base_index: int | None = None,
) -> str:
    """把 prompt 里的占位符替换成对应的"图片N"。

    - 表情占位符 `{happy}` / `{cry}` ... 替换为图片6/7/...（按 matched_order 顺序）
    - 场景占位符 `{scene:bedroom}` / `{scene:entrance}` ... 替换为表情之后的编号

    matched_scenes 留 None 时不处理场景占位符（向后兼容只用表情的旧调用方）。
    """
    for idx, expr_key in enumerate(matched_order):
        img_num = _EXPR_BASE_INDEX + idx
        for suffix in ("情绪图片", "表情图片", "情绪图", "表情图", "情绪参考", "表情参考", ""):
            prompt_text = prompt_text.replace(f"{{{expr_key}}}{suffix}", f"图片{img_num}")

    if matched_scenes:
        base = (
            scene_base_index
            if scene_base_index is not None
            else _EXPR_BASE_INDEX + len(matched_order)
        )
        for idx, sc in enumerate(matched_scenes):
            img_num = base + idx
            for suffix in ("场景图片", "场景参考图", "场景图", "场景参考", "参考图", "参考", ""):
                prompt_text = prompt_text.replace(f"{{scene:{sc}}}{suffix}", f"图片{img_num}")
    return prompt_text


def load_reference_images_for_prompt(prompt_text: str) -> tuple[list[str], list[str]]:
    """根据 prompt 动态选择参考图：固定5张 + 匹配到的表情图 + 匹配到的场景图。

    返回 (base64_list, labels)。labels 供日志/调试用。
    顺序：固定5张 → expression 图（按 match_expressions 顺序）→ scene 图（按 match_scenes 顺序）
    """
    images = load_all_reference_images()
    labels = ["主参考图", "手部特写", "张嘴侧面", "屁股特写", "全身正面"][:len(images)]

    for expr_key in match_expressions(prompt_text):
        path = REF_EXPRESSION_FILES.get(expr_key)
        if path and path.exists():
            with open(path, "rb") as f:
                images.append(base64.b64encode(f.read()).decode())
            labels.append(f"表情:{expr_key}")
            logger.info(f"表情参考图已加载: {expr_key} ({path.name})")

    for sc_key in match_scenes(prompt_text):
        path = REF_SCENE_FILES.get(sc_key)
        if path and path.exists():
            with open(path, "rb") as f:
                images.append(base64.b64encode(f.read()).decode())
            labels.append(f"场景:{sc_key}")
            logger.info(f"场景参考图已加载: {sc_key} ({path.name})")
    return images, labels


# ============================================================
# 图片声明注入（让 Seedance 知道每张附加图的用途）
# ============================================================

# 表情 key → 中文显示名
_EXPRESSION_ZH = {
    "happy": "开心",
    "laugh": "大笑",
    "cry":   "委屈哭泣",
    "shy":   "害羞",
    "angry": "生气奶凶",
}

# 场景 key → 中文显示名（与 REF_SCENE_FILES 同 key）
_SCENE_ZH = {
    "bedroom":         "蘑菇卧室（圆床+唱片机+绿色矮柜）",
    "living_bedroom":  "客厅+卧室（粉色沙发+橙色台灯+Monday日历）",
    "game_dressing":   "游戏室+衣帽间（开放衣架+镜子+红色瓶盖座椅）",
    "entrance":        "玄关（拱形木门+玄关矮柜+羊皮地毯）",
}


def build_image_declaration(prompt_text: str) -> tuple[str, list[str]]:
    """根据 prompt 内容构建"图片1是...图片2是...图片N是..."完整声明段。

    返回 (declaration_sentence, expression_keys)。
    expression_keys 仅包含表情 key（向后兼容）；场景图同样会进 declaration 但不在
    返回值里，调用方可自己 match_scenes(prompt) 拿场景列表。
    """
    parts = [
        "图片1是小蘑菇角色形象参考",
        "图片2是肢体末端（圆形无爪子）特写参考",
        "图片3是张嘴表情（嘴内黑色）特写参考",
        "图片4是屁股特写参考",
        "图片5是全身比例参考",
    ]
    matched = match_expressions(prompt_text)
    for i, expr in enumerate(matched):
        zh = _EXPRESSION_ZH.get(expr, expr)
        parts.append(f"图片{6 + i}是「{zh}」表情参考")
    scenes = match_scenes(prompt_text)
    scene_base = 6 + len(matched)
    for i, sc in enumerate(scenes):
        zh = _SCENE_ZH.get(sc, sc)
        parts.append(f"图片{scene_base + i}是「{zh}」场景参考")
    suffix_hint = "动作/表情" + ("/场景" if scenes else "")
    declaration = "。".join(parts) + f"。描述{suffix_hint}时可以显式引用对应图片。"
    return declaration, matched


def inject_image_declaration(prompt_text: str) -> str:
    """把完整图片声明注入 prompt 开头（规则生成，不依赖 LLM 的自述）。

    流程：
    1. **剥离** LLM 在开头自己写的"图片1是...图片2是...图片N是..."连续声明
       （LLM 经常漏写表情图对应的图片编号，所以不信任它的自述）
    2. **规则生成**完整声明（含匹配到的表情图）
    3. 把规则声明贴在剥离后的 prompt 开头
    """
    import re
    declaration, _ = build_image_declaration(prompt_text)

    # 剥离开头所有以"图片N是"开头的连续声明句（含"描述动作..."提示语）
    # 同时容忍换行、空白
    stripped = prompt_text
    # 先删一条类似"描述动作或表情时可以显式引用对应图片。"的提示句
    stripped = re.sub(
        r'^\s*(?:图片\d+[^。]*。\s*)+(?:描述[^。]*引用[^。]*。\s*)?',
        '',
        stripped,
        count=1,
    )
    return declaration + "\n\n" + stripped.lstrip()


def build_payload(
    prompt_text: str,
    img_b64: str | list[str],
    duration: int = None,
    video_b64: str | None = None,
) -> dict:
    """构建Seedance API payload。

    img_b64 可以是单张图片的 base64，也可以是多张图片的 list。
    video_b64 可选，作为 reference_video 角色传入（视觉约束比图片更强）。
    """
    images = [img_b64] if isinstance(img_b64, str) else img_b64
    content = [{"type": "text", "text": prompt_text}]
    for b in images:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b}"},
            "role": "reference_image"
        })
    if video_b64:
        content.append({
            "type": "video_url",
            "video_url": {"url": f"data:video/mp4;base64,{video_b64}"},
            "role": "reference_video"
        })
    return {
        "model": SEEDANCE_MODEL,
        "content": content,
        "generate_audio": True,
        "ratio": SEEDANCE_RATIO,
        "duration": duration or SEEDANCE_DURATION,
        "watermark": False
    }


def verify_payload(payload: dict) -> list[str]:
    """
    验证payload内容完整性。
    Batch 2教训：payload生成脚本bug导致text为空，Seedance自由发挥生成无关视频。
    """
    errors = []
    content = payload.get("content", [])
    text = content[0].get("text", "") if content else ""
    if len(text) < PROMPT_MIN_LENGTH:
        errors.append(f"payload text太短: {len(text)}字")
    if not text.startswith(REQUIRED_PREFIX):
        errors.append(f"payload text未以'{REQUIRED_PREFIX}'开头")
    has_image = (
        len(content) > 1
        and "base64" in str(content[1].get("image_url", {}).get("url", ""))[:50]
    )
    if not has_image:
        errors.append("payload缺少参考图片")
    return errors


def submit_task(prompt_text: str, img_b64: str | list[str],
                duration: int = None, payload_tag: str = "",
                video_b64: str | None = None) -> tuple[str | None, str | None]:
    """
    构建payload、验证并提交到Seedance API。
    img_b64 可传单张或多张参考图 base64。
    video_b64 可选，作为 reference_video 传入。
    返回 (task_id, error_message)。
    """
    api_key = get_ark_api_key()
    # 自动在 prompt 开头注入完整的图片声明段（让 Seedance 知道每张附加图的用途）
    # 注入策略：传了多张图时才注入，单图无需
    n_imgs = len(img_b64) if isinstance(img_b64, list) else 1
    if n_imgs > 1:
        # 1. 先根据 prompt 文本确定表情/场景图匹配顺序（同时识别关键词 + {placeholder}）
        matched = match_expressions(prompt_text)
        scenes = match_scenes(prompt_text)
        # 2. 在替换占位符之前先注入声明 —— inject_image_declaration 内部会再次
        #    match_expressions/match_scenes，必须在占位符还在的时候跑；否则像
        #    "{happy}+正文写'兴奋'"（"兴奋"不在 happy 关键词表）这种语境会漏匹配，
        #    声明里就会少一行参考图并和实际加载的图错位。
        prompt_text = inject_image_declaration(prompt_text)
        # 3. 最后把 {happy}/{cry}/{scene:bedroom}/... 占位符替换成图片编号
        prompt_text = resolve_expression_placeholders(prompt_text, matched, scenes)
    payload = build_payload(prompt_text, img_b64, duration, video_b64=video_b64)

    # 提交前验证
    errors = verify_payload(payload)
    if errors:
        return None, "; ".join(errors)

    # 序列化回读验证（Batch 2血泪教训：确保JSON序列化不丢内容）
    serialized = json.dumps(payload, ensure_ascii=False)
    check = json.loads(serialized)
    check_text = check["content"][0]["text"]
    if len(check_text) < PROMPT_MIN_LENGTH:
        return None, f"回读验证失败: text仅{len(check_text)}字"

    try:
        resp = _http_client.post(
            SEEDANCE_API_URL,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            content=serialized.encode("utf-8"),
            timeout=300,  # 大 payload（含 base64 图片 ~2MB），需要更长超时
        )
        data = resp.json()
        if "id" in data:
            _archive_prompt(data["id"], prompt_text, payload_tag)
            return data["id"], None
        elif "error" in data:
            err = data["error"]
            return None, f"{err.get('code', 'unknown')}: {err.get('message', '')[:80]}"
        else:
            return None, f"未知响应: {json.dumps(data)[:80]}"
    except httpx.TimeoutException:
        return None, "提交超时(120s)"
    except Exception as e:
        return None, str(e)


def query_task(task_id: str) -> dict:
    """查询单个任务状态。"""
    api_key = get_ark_api_key()
    try:
        resp = _http_client.get(
            f"{SEEDANCE_API_URL}/{task_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        return resp.json()
    except httpx.TimeoutException:
        return {"status": "error", "error": "查询超时"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def download_video(url: str, filepath, max_retries: int = 3) -> tuple[bool, str]:
    """下载视频文件（流式写入，含重试）。返回 (success, message)。

    已存在且大小 > 10KB 的文件会被视为已完成，直接返回成功（断点续传友好）。
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # 断点续传：已存在且合法就跳过
    if filepath.exists() and filepath.stat().st_size > 10000:
        size_mb = filepath.stat().st_size / (1024 * 1024)
        return True, f"已存在 {size_mb:.1f}MB"

    last_error = "未知错误"
    for attempt in range(1, max_retries + 1):
        try:
            with _http_client.stream("GET", url, timeout=180) as resp:
                resp.raise_for_status()
                with open(filepath, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        f.write(chunk)
            if filepath.exists() and filepath.stat().st_size > 10000:
                size_mb = filepath.stat().st_size / (1024 * 1024)
                return True, f"{size_mb:.1f}MB"
            last_error = "文件太小"
        except httpx.TimeoutException:
            last_error = f"下载超时(180s) [第{attempt}次]"
            logger.warning(last_error)
        except Exception as e:
            last_error = f"{e} [第{attempt}次]"
            logger.warning(last_error)
        # 清理失败的残片
        if filepath.exists() and filepath.stat().st_size <= 10000:
            filepath.unlink()
    return False, f"下载失败({max_retries}次重试): {last_error}"
