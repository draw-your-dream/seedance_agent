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
REF_IMAGE = PROJECT_ROOT / "reference.png"
REF_DIR = PROJECT_ROOT / "ref"

# 额外参考图（手部/张嘴特写，强化角色外貌约束）
REF_HAND_CLOSEUP = REF_DIR / "1.png"     # 手部特写：圆手无手指
REF_MOUTH_SIDE = REF_DIR / "15.png"       # 张嘴侧面：口腔内部
REF_FULL_BODY = REF_DIR / "34.png"        # 全身正面：嘴+手+比例综合

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
