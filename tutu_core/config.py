# -*- coding: utf-8 -*-
"""
集中配置管理 — 所有API密钥和路径统一在此管理。

API密钥从环境变量读取。本地开发请复制 .env.example 为 .env 并填写密钥。
"""

import os
import logging
from pathlib import Path

# 尝试加载 .env 文件（python-dotenv 为可选依赖）
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# ============================================================
# 统一日志配置
# ============================================================

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

def setup_logging():
    """配置 tutu 命名空间下的统一日志格式。"""
    root_logger = logging.getLogger("tutu")
    if root_logger.handlers:
        return  # 已初始化，避免重复添加 handler
    root_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    ))
    root_logger.addHandler(handler)

setup_logging()
logger = logging.getLogger("tutu")

# ============================================================
# 路径
# ============================================================

PROJECT_ROOT = Path(__file__).parent.parent
REF_IMAGE_ORIGINAL = PROJECT_ROOT / "reference.png"
REF_DIR = PROJECT_ROOT / "ref"
REF_PROCESSED_DIR = REF_DIR / "processed"
REF_IMAGE = REF_PROCESSED_DIR / "reference.jpg"  # 压缩版 (59KB)，原图 1.7MB

# 额外参考图（预处理后：裁切+缩放+压缩，长边≤1024px）
REF_HAND_CLOSEUP = REF_PROCESSED_DIR / "hand_closeup.jpg"    # 手部特写：圆手无手指 (65KB)
REF_MOUTH_SIDE = REF_PROCESSED_DIR / "mouth_closeup.jpg"     # 张嘴脸部裁切 (95KB)
REF_FULL_BODY = REF_PROCESSED_DIR / "full_body.jpg"          # 全身正面 (22KB)

# 表情参考图（按需注入，prompt 含相关关键词时才附加）
REF_EXPRESSIONS_DIR = REF_PROCESSED_DIR / "expressions"
REF_EXPRESSION_FILES = {
    "happy": REF_EXPRESSIONS_DIR / "happy.jpg",
    "laugh": REF_EXPRESSIONS_DIR / "laugh.jpg",
    "cry":   REF_EXPRESSIONS_DIR / "cry.jpg",
    "shy":   REF_EXPRESSIONS_DIR / "shy.jpg",
    "angry": REF_EXPRESSIONS_DIR / "angry.jpg",
}

# 表情关键词映射：在 prompt 中出现任一关键词即触发对应表情参考图
EXPRESSION_KEYWORDS = {
    "happy": ["开心", "高兴", "满足", "喜悦", "雀跃", "嘟嘟！", "眯眼笑", "腮帮子鼓"],
    "laugh": ["大笑", "哈哈", "咧嘴", "笑开了花", "笑出声", "捧腹"],
    "cry":   ["大哭", "哭", "委屈", "流泪", "眼泪", "含泪", "哽咽", "呜呜"],
    "shy":   ["害羞", "脸红", "躲到帽子", "从帽子下", "心虚", "偷偷看", "羞涩"],
    "angry": ["生气", "奶凶", "气呼呼", "鼓脸", "嘟嘴瞪", "嘟——！！"],
}

PROMPT_SYSTEM_DIR = PROJECT_ROOT / "prompt生成系统"
OUTPUT_DIR = PROMPT_SYSTEM_DIR / "output"
VIDEO_DIR = OUTPUT_DIR / "videos"

V2_DIR = PROMPT_SYSTEM_DIR / "v2"
PERSONALITY_FILE = V2_DIR / "personality.md"
IP_CONSTITUTION_FILE = PROMPT_SYSTEM_DIR / "ip-constitution.md"
LIFE_JOURNAL_FILE = V2_DIR / "life_journal.json"
USER_MEMORY_FILE = V2_DIR / "user_memory.json"
DAILY_SIGNALS_FILE = V2_DIR / "daily_signals.json"

APP_DIR = PROJECT_ROOT / "app"
DB_PATH = APP_DIR / "tutu.db"

# prompt 归档目录：submit_task 成功后将 prompt 文本按 task_id 落盘
PROMPT_ARCHIVE_DIR = PROJECT_ROOT / "prompt生成系统" / "output" / "submitted_prompts"

# ============================================================
# API密钥（从环境变量读取）
# ============================================================

def _require_key(name: str) -> str:
    """获取必需的API密钥，缺失时抛出清晰的错误信息。"""
    val = os.environ.get(name)
    if not val:
        raise EnvironmentError(
            f"环境变量 {name} 未设置。"
            f"请复制 .env.example 为 .env 并填写API密钥。"
        )
    return val


def get_ark_api_key() -> str:
    return _require_key("ARK_API_KEY")


def get_gemini_api_key() -> str:
    return _require_key("GEMINI_API_KEY")


# ============================================================
# API端点
# ============================================================

GEMINI_URL = os.environ.get(
    "GEMINI_URL",
    "https://ai.ssnai.com/gemini/v1beta/models/gemini-2.0-flash:generateContent"
)

SEEDANCE_API_URL = "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"
ARK_CHAT_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"

# ============================================================
# Seedance 配置
# ============================================================

SEEDANCE_MODEL = "doubao-seedance-2-0-260128"
SEEDANCE_CONCURRENCY = 2
SEEDANCE_DURATION = 13
SEEDANCE_RATIO = "9:16"

# ============================================================
# LLM 配置
# ============================================================

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini")
ARK_LLM_MODEL = "doubao-1-5-pro-256k-250115"

# prompt 生成版本切换：v1(默认，保留不动) / v2(基于示例 prompt 迭代的新版)
GENERATION_VERSION = os.environ.get("GENERATION_VERSION", "v1").lower()

# ============================================================
# Admin
# ============================================================

ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "120"))

# ============================================================
# 校验规则常量
# ============================================================

FORBIDDEN_WORDS = ["手指", "牙齿", "舌头", "爪子", "眉毛"]
AGGRESSIVE_WORDS = ["暴怒", "打砸", "奶凶", "死死", "疯狂", "气呼呼", "抢", "砸", "撕"]
NEGATION_KEYWORDS = ["没有", "不出现", "不要", "注意：", "禁止", "不能"]
REQUIRED_SUFFIXES = ["黑色", "小肉球", "没有尾巴"]
REQUIRED_PREFIX = "图片1"
PROMPT_MIN_LENGTH = 300
PROMPT_MAX_LENGTH = 900
PROMPT_TARGET_LENGTH = 600
