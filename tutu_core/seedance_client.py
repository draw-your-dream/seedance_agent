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
    REQUIRED_PREFIX, PROMPT_MIN_LENGTH,
)

logger = logging.getLogger("tutu.seedance")


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
    """加载主参考图 + 额外特写参考图（手部/张嘴/全身）。

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
