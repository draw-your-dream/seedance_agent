# -*- coding: utf-8 -*-
"""统一LLM调用客户端 — 支持 Gemini / Claude / ARK / OpenAI，含缓存和并发"""

import json
import hashlib
import logging
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from tutu_core.config import (
    get_gemini_api_key, get_ark_api_key,
    GEMINI_URL, ARK_CHAT_URL, ARK_LLM_MODEL, LLM_PROVIDER,
)

# 共享 httpx 客户端：连接池 + keep-alive，避免每次调用新建 TCP 连接
_http_client = httpx.Client(timeout=120, follow_redirects=True)

logger = logging.getLogger("tutu.llm")


# ============================================================
# 简易内存缓存（避免相同 prompt 重复调用浪费 token）
# ============================================================

_cache_lock = threading.Lock()
_cache: OrderedDict[str, str] = OrderedDict()
_CACHE_MAX_SIZE = 200


def _cache_key(system: str, user: str, provider: str) -> str:
    raw = f"{provider}:{system}:{user}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def clear_cache():
    """清空LLM缓存。"""
    with _cache_lock:
        _cache.clear()


# ============================================================
# JSON 提取（修复贪心正则 bug）
# ============================================================

def extract_json(text: str, expect_array: bool = False):
    """
    从LLM输出中稳健提取JSON。

    处理方式（按优先级）：
    1. ```json 代码块
    2. ``` 通用代码块
    3. 平衡括号匹配（替代贪心正则 r'{.*}'）
    """
    # 1. 尝试 ```json 代码块
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        try:
            return json.loads(text[start:end].strip())
        except (json.JSONDecodeError, ValueError):
            pass  # 回退到下一个策略

    # 2. 尝试通用代码块
    if "```" in text:
        start = text.index("```") + 3
        newline = text.find("\n", start)
        if newline > 0:
            start = newline + 1
        end_marker = text.find("```", start)
        if end_marker > 0:
            candidate = text[start:end_marker].strip()
            try:
                return json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                pass

    # 3. 平衡括号匹配（可能有多个候选，逐一尝试）
    opener = "[" if expect_array else "{"
    closer = "]" if expect_array else "}"
    search_start = 0
    while True:
        start = text.find(opener, search_start)
        if start < 0:
            break
        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(text)):
            c = text[i]
            if escape_next:
                escape_next = False
                continue
            if c == '\\':
                escape_next = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except (json.JSONDecodeError, ValueError):
                    break  # 这个候选失败，搜索下一个
        search_start = start + 1

    raise ValueError(f"无法从LLM输出中提取JSON: {text[:200]}...")


# ============================================================
# 统一调用入口
# ============================================================

def call_llm(system_prompt: str, user_prompt: str,
             max_tokens: int = 4000, provider: str = None,
             use_cache: bool = True) -> str | None:
    """
    调用LLM，返回文本结果或None。

    use_cache=True 时，相同 (system+user+provider) 的请求会命中缓存。
    """
    provider = (provider or LLM_PROVIDER).lower()

    # 查缓存
    if use_cache:
        key = _cache_key(system_prompt, user_prompt, provider)
        with _cache_lock:
            if key in _cache:
                _cache.move_to_end(key)  # LRU: 标记为最近使用
                logger.debug("LLM缓存命中")
                return _cache[key]

    handlers = {
        "gemini": _call_gemini,
        "claude": _call_claude,
        "ark": _call_ark,
        "openai": _call_openai,
    }
    handler = handlers.get(provider)
    if not handler:
        logger.error(f"不支持的LLM提供商: {provider}")
        return None
    result = handler(system_prompt, user_prompt, max_tokens)

    # 写缓存
    if use_cache and result:
        with _cache_lock:
            cache_key = _cache_key(system_prompt, user_prompt, provider)
            if cache_key in _cache:
                _cache.move_to_end(cache_key)
            _cache[cache_key] = result
            while len(_cache) > _CACHE_MAX_SIZE:
                _cache.popitem(last=False)  # LRU: 淘汰最久未使用的

    return result


# ============================================================
# 并发批量调用
# ============================================================

def call_llm_batch(requests: list[dict], max_workers: int = 3) -> list[str | None]:
    """
    并发调用多个LLM请求。

    requests: [{"system": str, "user": str, "max_tokens": int}, ...]
    返回: 与 requests 同序的结果列表
    """
    results = [None] * len(requests)

    def _call(idx, req):
        return idx, call_llm(
            req.get("system", ""),
            req["user"],
            req.get("max_tokens", 4000),
        )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_call, i, r): i for i, r in enumerate(requests)}
        for future in as_completed(futures):
            try:
                idx, result = future.result()
                results[idx] = result
            except Exception as e:
                idx = futures[future]
                logger.error(f"批量调用[{idx}]失败: {e}")

    return results


# ============================================================
# 各Provider实现
# ============================================================

def _call_gemini(system_prompt: str, user_prompt: str, max_tokens: int = 4000) -> str | None:
    try:
        api_key = get_gemini_api_key()
        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": f"{system_prompt}\n\n---\n\n{user_prompt}"}]}
            ]
        }
        resp = _http_client.post(
            GEMINI_URL,
            headers={"Content-Type": "application/json", "X-goog-api-key": api_key},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        logger.error(f"Gemini调用失败: {e}")
        return None


def _call_claude(system_prompt: str, user_prompt: str, max_tokens: int = 4000) -> str | None:
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return msg.content[0].text
    except ImportError:
        logger.warning("anthropic SDK未安装，回退到ARK")
        return _call_ark(system_prompt, user_prompt, max_tokens)
    except Exception as e:
        logger.error(f"Claude调用失败: {e}")
        return None


def _call_ark(system_prompt: str, user_prompt: str, max_tokens: int = 4000) -> str | None:
    try:
        api_key = get_ark_api_key()
        payload = {
            "model": ARK_LLM_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": max_tokens
        }
        resp = _http_client.post(
            ARK_CHAT_URL,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"ARK调用失败: {e}")
        return None


def _call_openai(system_prompt: str, user_prompt: str, max_tokens: int = 4000) -> str | None:
    try:
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return resp.choices[0].message.content
    except ImportError:
        logger.error("openai SDK未安装: pip install openai")
        return None
    except Exception as e:
        logger.error(f"OpenAI调用失败: {e}")
        return None
