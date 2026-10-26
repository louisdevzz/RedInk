"""
认证模块

目前包含 Google Antigravity（Cloud Code Assist）的 OAuth 登录：
- 文本 / 图片生成可以直接使用 Google 账号登录后的配额，无需 API Key
- API Key 方式依旧保留，作为可选方案（openai_compatible / google_gemini / google_genai ...）

配置里出现下面任意一种情况，就表示该服务商不需要 api_key：
    auth: antigravity_oauth
    type: google_antigravity
"""

from .antigravity_constants import (
    IMAGE_MODELS as ANTIGRAVITY_IMAGE_MODELS,
    PROVIDER_TYPE as ANTIGRAVITY_PROVIDER_TYPE,
    DEFAULT_MODEL as DEFAULT_ANTIGRAVITY_MODEL,
    strip_effort_annotation,
)

# 服务商配置里标记 OAuth 的字段值
AUTH_ANTIGRAVITY_OAUTH = 'antigravity_oauth'

# 未配置服务商时的默认方案：Google 账号登录（Antigravity OAuth），不需要 API Key
ANTIGRAVITY_PROVIDER_NAME = 'antigravity'


def uses_oauth_auth(provider_config: dict) -> bool:
    """
    判断服务商是否使用 OAuth 认证（OAuth 服务商的 api_key 是可选的）

    Args:
        provider_config: 服务商配置字典

    Returns:
        bool: True 表示无需强制校验 API Key
    """
    if not provider_config:
        return False

    return provider_config.get('type') == ANTIGRAVITY_PROVIDER_TYPE or provider_config.get('auth') == AUTH_ANTIGRAVITY_OAUTH


def antigravity_provider_config(**extra) -> dict:
    """构造一个 Antigravity OAuth 服务商配置（可覆盖 model 等字段）。"""
    config = {'type': ANTIGRAVITY_PROVIDER_TYPE, 'auth': AUTH_ANTIGRAVITY_OAUTH, 'model': 'auto'}
    config.update(extra)
    return config


def default_text_providers() -> dict:
    """文本生成的出厂默认：Antigravity OAuth（登录即用，无需 API Key）。"""
    return {
        'active_provider': ANTIGRAVITY_PROVIDER_NAME,
        'providers': {
            ANTIGRAVITY_PROVIDER_NAME: antigravity_provider_config(
                temperature=1.0, max_output_tokens=8000
            )
        },
    }


def default_image_providers() -> dict:
    """图片生成的出厂默认：Antigravity OAuth（登录即用，无需 API Key）。"""
    return {
        'active_provider': ANTIGRAVITY_PROVIDER_NAME,
        'providers': {ANTIGRAVITY_PROVIDER_NAME: antigravity_provider_config()},
    }


def missing_credential_error(provider_name: str, provider_type: str = None) -> ValueError:
    """构造“缺少凭证”的用户可读错误（区分 OAuth 与 API Key 两种方案）。"""
    if uses_oauth_auth({'type': provider_type}):
        return ValueError(
            f"Nhà cung cấp {provider_name} 尚未登录 tài khoản Google\n"
            "Giải pháp:\n"
            "1. Mở trang Cài đặt hệ thống, đăng nhập tài khoản Antigravity trong thẻ 'Đăng nhập tài khoản Google'\n"
            "2. Sau khi đăng nhập thành công không cần điền API Key"
        )
    return ValueError(
        f"Nhà cung cấp {provider_name} 未配置 API Key\n"
        "Giải pháp:\n"
        "1. Chỉnh sửa nhà cung cấp này trong trang Cài đặt hệ thống, điền API Key\n"
        "2. Hoặc chuyển sang đăng nhập tài khoản Google (Antigravity OAuth), không cần API Key"
    )


__all__ = [
    'ANTIGRAVITY_IMAGE_MODELS',
    'ANTIGRAVITY_PROVIDER_NAME',
    'ANTIGRAVITY_PROVIDER_TYPE',
    'AUTH_ANTIGRAVITY_OAUTH',
    'DEFAULT_ANTIGRAVITY_MODEL',
    'antigravity_provider_config',
    'default_image_providers',
    'default_text_providers',
    'missing_credential_error',
    'strip_effort_annotation',
    'uses_oauth_auth',
]
