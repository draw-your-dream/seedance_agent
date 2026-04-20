# -*- coding: utf-8 -*-
"""Seedance视频生成API客户端 — 统一提交/查询/下载逻辑"""

import json
import base64
import logging
from pathlib import Path

import httpx

# 共享客户端：连接池复用
_http_client = httpx.Client(timeout=120, follow_redirects=True)

from tutu_core.config import (
    get_ark_api_key, SEEDANCE_API_URL, SEEDANCE_MODEL,
    SEEDANCE_DURATION, SEEDANCE_RATIO,
    REF_IMAGE, REF_IMAGE_ORIGINAL,
    REF_HAND_CLOSEUP, REF_MOUTH_SIDE, REF_FULL_BODY,
    REF_EXPRESSION_FILES, EXPRESSION_KEYWORDS,
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
    """加载主参考图 + 固定的3张特写（手部/张嘴/全身）。

    返回 base64 列表，第一张是主参考图（图片1），后续是特写补充。
    只加载存在的文件，不会因缺失特写而报错。
    """
    images = [load_reference_image()]  # 主参考图必须存在

    extras = [
        (REF_HAND_CLOSEUP, "手部特写"),
        (REF_MOUTH_SIDE, "张嘴侧面"),
        (REF_FULL_BODY, "全身正面"),
    ]
    for path, label in extras:
        if path.exists():
            with open(path, "rb") as f:
                images.append(base64.b64encode(f.read()).decode())
            logger.info(f"额外参考图已加载: {label} ({path.name})")
    return images


def match_expressions(prompt_text: str) -> list[str]:
    """根据 prompt 文本关键词匹配出应该附加的表情参考图 key 列表。

    返回命中的表情 key（如 ["happy", "cry"]），去重且按 EXPRESSION_KEYWORDS 声明顺序。
    """
    matched = []
    for expr_key, keywords in EXPRESSION_KEYWORDS.items():
        if any(kw in prompt_text for kw in keywords):
            matched.append(expr_key)
    return matched


def load_reference_images_for_prompt(prompt_text: str) -> tuple[list[str], list[str]]:
    """根据 prompt 动态选择参考图：固定4张 + 匹配到的表情图。

    返回 (base64_list, labels)。labels 供日志/调试用。
    """
    images = load_all_reference_images()
    labels = ["主参考图", "手部特写", "张嘴侧面", "全身正面"][:len(images)]

    for expr_key in match_expressions(prompt_text):
        path = REF_EXPRESSION_FILES.get(expr_key)
        if path and path.exists():
            with open(path, "rb") as f:
                images.append(base64.b64encode(f.read()).decode())
            labels.append(f"表情:{expr_key}")
            logger.info(f"表情参考图已加载: {expr_key} ({path.name})")
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


def build_image_declaration(prompt_text: str) -> tuple[str, list[str]]:
    """根据 prompt 内容构建"图片1是...图片2是...图片N是..."完整声明段。

    返回 (declaration_sentence, expression_keys)。
    """
    parts = [
        "图片1是小蘑菇角色形象参考",
        "图片2是肢体末端（圆形无爪子）特写参考",
        "图片3是张嘴表情（嘴内黑色）特写参考",
        "图片4是全身比例参考",
    ]
    matched = match_expressions(prompt_text)
    for i, expr in enumerate(matched):
        zh = _EXPRESSION_ZH.get(expr, expr)
        parts.append(f"图片{5 + i}是「{zh}」表情参考")
    declaration = "。".join(parts) + "。描述动作或表情时可以显式引用对应图片。"
    return declaration, matched


def inject_image_declaration(prompt_text: str) -> str:
    """把完整图片声明注入 prompt 开头，替换原有的"图片1是小蘑菇角色形象参考。"。

    幂等：如果 prompt 已包含"图片2是"或"图片3是"则不重复注入。
    """
    if "图片2是" in prompt_text or "图片3是" in prompt_text:
        return prompt_text  # 已有完整声明
    declaration, _ = build_image_declaration(prompt_text)
    # 找原有的 "图片1是..." 片段并替换
    # 匹配第一句以"图片1"开头到第一个"。"
    import re
    m = re.match(r'(图片1[^。]*。)', prompt_text)
    if m:
        return declaration + prompt_text[m.end():]
    # 兜底：如果 prompt 不以图片1开头，直接在前面拼
    return declaration + prompt_text


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
        prompt_text = inject_image_declaration(prompt_text)
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
