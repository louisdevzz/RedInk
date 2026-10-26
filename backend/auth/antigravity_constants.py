"""
Google Antigravity（Cloud Code Assist）协议常量

来源：oh-my-pi（can1357/oh-my-pi）中已线上验证的实现：
- packages/catalog/src/compat/rules/auth/google-antigravity.kdl（OAuth 参数）
- packages/catalog/src/discovery/antigravity.ts（模型发现）
- packages/catalog/src/wire/gemini-headers.ts（User-Agent / wire profile）
- packages/ai/src/providers/google-gemini-cli.ts（请求信封与响应解析）
- packages/ai/src/images/google-antigravity.ts（图片生成）
"""
import base64
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ==================== OAuth ====================

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo?alt=json"

# Antigravity 使用与 Gemini CLI 不同的 Google OAuth 客户端凭据。
# 出于安全考虑不随源码分发：通过环境变量 REDINK_ANTIGRAVITY_CLIENT_ID /
# REDINK_ANTIGRAVITY_CLIENT_SECRET 提供，或放在仓库根目录的 .antigravity_client.json
# （已 gitignore，只存在于本地）。两者都缺失时登录接口会明确报错，而不是回退内置密钥。
_OAUTH_CLIENT_FILE = Path(__file__).resolve().parent.parent.parent / ".antigravity_client.json"


def _load_oauth_client() -> tuple:
    """读取公开 OAuth 客户端凭据：环境变量优先，其次本地文件；缺失则返回空串。"""
    client_id = os.environ.get("REDINK_ANTIGRAVITY_CLIENT_ID") or ""
    client_secret = os.environ.get("REDINK_ANTIGRAVITY_CLIENT_SECRET") or ""
    if client_id and client_secret:
        return client_id, client_secret
    try:
        with _OAUTH_CLIENT_FILE.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    return (client_id or str(data.get("client_id") or ""),
            client_secret or str(data.get("client_secret") or ""))


OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET = _load_oauth_client()

OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]

# 与官方客户端一致的 loopback 回调地址（Google 允许 127.0.0.1 回环）
CALLBACK_HOSTNAME = "127.0.0.1"
CALLBACK_PORT = int(os.environ.get("REDINK_ANTIGRAVITY_PORT", "51121"))
CALLBACK_PATH = "/oauth-callback"
LOGIN_TIMEOUT_SECONDS = 300

# 写入凭据时预留 5 分钟，避免边界上拿到即将过期的 token
CREDENTIAL_SKEW_MS = 300_000
# 距离过期 60s 以内即触发刷新（ANTIGRAVITY_REFRESH_SKEW_MS）
REFRESH_SKEW_MS = 60_000

# ==================== Cloud Code Assist 接口 ====================

PROVIDER_TYPE = "google_antigravity"

DAILY_ENDPOINT = "https://daily-cloudcode-pa.googleapis.com"
SANDBOX_ENDPOINT = "https://daily-cloudcode-pa.sandbox.googleapis.com"
ENDPOINT_FALLBACKS = [DAILY_ENDPOINT, SANDBOX_ENDPOINT]

LOAD_CODE_ASSIST_PATH = "/v1internal:loadCodeAssist"
ONBOARD_USER_PATH = "/v1internal:onboardUser"
FETCH_AVAILABLE_MODELS_PATH = "/v1internal:fetchAvailableModels"
RETRIEVE_QUOTA_PATH = "/v1internal:retrieveUserQuotaSummary"
GENERATE_CONTENT_PATH = "/v1internal:generateContent"
STREAM_GENERATE_CONTENT_PATH = "/v1internal:streamGenerateContent?alt=sse"

FREE_TIER_ID = "free-tier"
ANTIGRAVITY_METADATA = {"ideType": "ANTIGRAVITY"}

ONBOARD_TIMEOUT_SECONDS = 30
ONBOARD_POLL_INTERVAL_SECONDS = 1

# ==================== User-Agent ====================

# 后端会按客户端版本放行较新的模型，因此 UA 版本必须足够新
DEFAULT_ANTIGRAVITY_VERSION = "2.19.1"
ANTIGRAVITY_VERSION_MANIFEST_URL = (
    "https://antigravity-hub-auto-updater-974169037036.us-central1.run.app/manifest/latest-arm64-mac.yml"
)
VERSION_FETCH_TIMEOUT_SECONDS = 5
VERSION_RETRY_SECONDS = 600

_antigravity_version = None
_antigravity_version_failed_at = None

# 服务商类型别名（配置里写 type 或 auth 都识别为 OAuth 服务商）
DEFAULT_MODEL = "gemini-3.1-pro-low"
DEFAULT_IMAGE_MODEL = "gemini-3-pro-image"

# 已验证的常用文本模型（bare id，不能带 (high)/(low) 这类注释，否则 404）
COMMON_TEXT_MODELS = [
    "gemini-3.1-pro-low",
    "gemini-3-pro-agent",
    "gemini-3.5-flash-low",
    "gemini-3.5-flash-extra-low",
    "gemini-3-flash-agent",
    "claude-sonnet-4-6",
    "claude-opus-4-6-thinking",
]

# 账号侧可能出现的图片模型（实际以 fetchAvailableModels 的 imageGenerationModelIds 为准）
IMAGE_MODELS = ["gemini-3-pro-image", "gemini-3.1-flash-image"]

# 发现接口里的黑名单：列出了但推理必定 404
DISCOVERY_DENYLIST = {"chat_20706", "chat_23310", "gemini-2.5-pro"}

# 每个 wire id 固定的 maxOutputTokens / model_enum（服务端会强制，Claude 超过 64000 直接 400）
MODEL_WIRE_PROFILES = {
    "gemini-3.5-flash-extra-low": {"model_enum": "MODEL_PLACEHOLDER_M187", "max_output_tokens": 65536},
    "gemini-3.5-flash-low": {"model_enum": "MODEL_PLACEHOLDER_M20", "max_output_tokens": 65536},
    "gemini-3-flash-agent": {"model_enum": "MODEL_PLACEHOLDER_M132", "max_output_tokens": 65536},
    "gemini-3.1-pro-low": {"model_enum": "MODEL_PLACEHOLDER_M36", "max_output_tokens": 65535},
    "gemini-pro-agent": {"model_enum": "MODEL_PLACEHOLDER_M16", "max_output_tokens": 65535},
    "claude-sonnet-4-6": {"max_output_tokens": 64000},
    "claude-opus-4-6-thinking": {"max_output_tokens": 64000},
}

_EFFORT_ANNOTATION_RE = re.compile(r"\([^()]+\)\s*$")


def strip_effort_annotation(model_id: str) -> str:
    """去掉 'gemini-3-pro (high)' 这类注释后缀，接口只接受裸模型 id。"""
    return _EFFORT_ANNOTATION_RE.sub("", model_id or "").strip()


def model_wire_profile(model_id: str) -> dict:
    return MODEL_WIRE_PROFILES.get(strip_effort_annotation(model_id), {})


def antigravity_user_agent() -> str:
    """Antigravity 客户端 UA；版本号会尝试从官方更新清单发现，失败则使用固定回退值。"""
    global _antigravity_version, _antigravity_version_failed_at

    override = os.environ.get("REDINK_ANTIGRAVITY_VERSION")
    if not override and _antigravity_version is None:
        now = time.time()
        if _antigravity_version_failed_at is None or now - _antigravity_version_failed_at >= VERSION_RETRY_SECONDS:
            _antigravity_version = _discover_version()
            if _antigravity_version is None:
                _antigravity_version_failed_at = now
    version = override or _antigravity_version or DEFAULT_ANTIGRAVITY_VERSION
    cl = os.environ.get("REDINK_ANTIGRAVITY_CL", "963137146")
    os_name = os.environ.get("REDINK_ANTIGRAVITY_OS", "darwin")
    arch = os.environ.get("REDINK_ANTIGRAVITY_ARCH", "arm64")
    return f"antigravity/hub/{version} (aidev_client; os_type={os_name}; arch={arch}; cl={cl})"


def _discover_version():
    """读取 electron-builder 更新清单里的 version，失败静默回退。"""
    try:
        import requests

        response = requests.get(
            ANTIGRAVITY_VERSION_MANIFEST_URL,
            headers={"Cache-Control": "no-cache", "User-Agent": "electron-builder"},
            timeout=VERSION_FETCH_TIMEOUT_SECONDS,
        )
        if not response.ok:
            return None
        for line in response.text.splitlines():
            match = re.match(r'^\s*version\s*:\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s#]+))\s*(?:#.*)?$', line)
            if not match:
                continue
            version = (match.group(1) or match.group(2) or match.group(3) or "").strip()
            return version if re.match(r"^\d+\.\d+\.\d+$", version) else None
    except Exception as exc:  # 版本发现失败不影响请求，回退版本号仍然有效
        logger.debug(f"Antigravity 版本发现失败，使用固定版本: {exc}")
    return None


def sha256_b64url(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
