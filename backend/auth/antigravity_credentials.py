"""
Antigravity（Cloud Code Assist）OAuth 凭据存储与刷新（多账号池）

凭据保存在项目根目录的 .antigravity_credentials.json（已加入 .gitignore）。
文件内容是账号池：每个 Google 账号一条记录，各自持有 access/refresh token、
project id 与配额冷却状态；旧版单账号格式读取时自动迁移。

选号策略（参考 oh-my-pi auth-broker）：
- 取 token 时选"健康"账号（未失效、未冷却），按最久未使用轮转；
- 上游对某账号 429 / 配额错误 -> 该账号进入冷却（尊重 quotaResetTimeStamp），请求切到下一个账号；
- 刷新遇到 invalid_grant -> 该账号标记失效（其余账号继续服务）；
- 池里只有一个账号时行为与单账号时期完全一致（退避重试，不轮转）。
"""
import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

import requests

from .antigravity_constants import (
    CREDENTIAL_SKEW_MS,
    OAUTH_CLIENT_ID,
    OAUTH_CLIENT_SECRET,
    REFRESH_SKEW_MS,
    TOKEN_URL,
    USERINFO_URL,
    antigravity_user_agent,
)
from .antigravity_pool import (
    DEFAULT_QUOTA_COOLDOWN_MS,
    DEFAULT_TRANSIENT_COOLDOWN_MS,
    account_id_for,
    account_summary,
    clear_block,
    clear_pool,
    healthy_accounts,
    is_blocked,
    list_accounts,
    mark_blocked,
    mark_dead,
    parse_reset_time,
    pick_account,
    pool_summary,
    read_pool,
    remove_account,
    touch_account,
    upsert_account,
)

logger = logging.getLogger(__name__)

CREDENTIALS_FILE = Path(__file__).parent.parent.parent / '.antigravity_credentials.json'

_lock = threading.RLock()

# 最近一次对外发放 token 的账号（进程内粘性，避免每次请求重新挑选）
_active_account_id: Optional[str] = None


class AntigravityAuthError(Exception):
    """未登录 / 凭据不可用（需要重新登录）。"""


def _now_ms() -> int:
    return int(time.time() * 1000)


# ==================== 池读写（保持单账号时期的函数签名） ====================

def load_credentials() -> Optional[dict]:
    """读取当前账号凭据（单账号调用方的兼容视图）；未登录返回 None。"""
    with _lock:
        return pick_account(CREDENTIALS_FILE, prefer_id=_active_account_id)


def save_credentials(
    token: str,
    refresh_token: str = None,
    expires_at: int = None,
    project_id: str = None,
    email: str = None,
    account_id: str = None,
) -> dict:
    """写入账号（同 id 覆盖，新账号追加 = 再登录一个 Google 账号即加入池）。"""
    with _lock:
        record = upsert_account(
            CREDENTIALS_FILE,
            token=token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            project_id=project_id,
            email=email,
            account_id=account_id,
        )
        global _active_account_id
        _active_account_id = record['id']

        # 通知依赖缓存（生成器/客户端）凭据已变化
        _invalidate_dependents()
        return record


def clear_credentials():
    """退出登录：删除整个账号池。"""
    global _active_account_id
    with _lock:
        clear_pool(CREDENTIALS_FILE)
        _active_account_id = None
        _invalidate_dependents()


def clear_account(account_id: str) -> bool:
    """退出单个账号；账号不存在返回 False。"""
    global _active_account_id
    with _lock:
        removed = remove_account(CREDENTIALS_FILE, account_id)
        if removed and _active_account_id == account_id:
            _active_account_id = None
            _invalidate_dependents()
        return removed


def account_list() -> list:
    """全部账号状态（不含 token），供设置页展示。"""
    with _lock:
        return [account_summary(a) for a in list_accounts(CREDENTIALS_FILE)]


# ==================== 选号与轮换 ====================

def current_account_id() -> Optional[str]:
    """最近一次发放 token 的账号 id（调用方用于记账）。"""
    with _lock:
        return _active_account_id


def account_ids() -> list:
    with _lock:
        return [a['id'] for a in read_pool(CREDENTIALS_FILE)]


def rotation_available(exclude: list = None) -> bool:
    """池里是否还有可切换的账号（排除已尝试过的）。"""
    with _lock:
        return bool(healthy_accounts(CREDENTIALS_FILE, exclude=exclude or []))


def note_quota_exhausted(detail=None, reason: str = None) -> Optional[int]:
    """
    当前账号被限流/配额耗尽：进入冷却，返回冷却时长（毫秒）。

    只有一个账号时不拉黑（否则用户会被自己锁死），只返回冷却提示。
    """
    global _active_account_id
    with _lock:
        account_id = _active_account_id
        if not account_id:
            return None
        accounts = read_pool(CREDENTIALS_FILE)
        if len(accounts) <= 1:
            return None

        until = parse_reset_time(detail, now=_now_ms(), default_ms=DEFAULT_QUOTA_COOLDOWN_MS)
        mark_blocked(CREDENTIALS_FILE, account_id, until, reason=reason)
        cooldown = max(0, until - _now_ms())
        _active_account_id = None
        return cooldown


def note_credentials_dead(reason: str = None) -> None:
    """当前账号凭据不可用（撤销/无法刷新）：标记失效并换号。"""
    global _active_account_id
    with _lock:
        account_id = _active_account_id
        if not account_id:
            return
        accounts = read_pool(CREDENTIALS_FILE)
        if len(accounts) <= 1:
            clear_pool(CREDENTIALS_FILE)
            _invalidate_dependents()
        else:
            mark_dead(CREDENTIALS_FILE, account_id, reason=reason)
        _active_account_id = None


def note_refresh_failed(reason: str = None) -> Optional[int]:
    """刷新暂时失败（网络/5xx）：短冷却后换下一个账号。"""
    global _active_account_id
    with _lock:
        account_id = _active_account_id
        if not account_id:
            return None
        accounts = read_pool(CREDENTIALS_FILE)
        if len(accounts) <= 1:
            return None
        until = _now_ms() + DEFAULT_TRANSIENT_COOLDOWN_MS
        mark_blocked(CREDENTIALS_FILE, account_id, until, reason=reason)
        _active_account_id = None
        return DEFAULT_TRANSIENT_COOLDOWN_MS


def unblock_account(account_id: str) -> bool:
    """手动解除某账号的配额冷却（设置页"恢复"按钮）。"""
    with _lock:
        exists = any(a['id'] == account_id for a in read_pool(CREDENTIALS_FILE))
        if exists:
            clear_block(CREDENTIALS_FILE, account_id)
            _invalidate_dependents()
        return exists


def needs_refresh(credentials: dict) -> bool:
    expires_at = credentials.get('expires_at')
    if not isinstance(expires_at, (int, float)) or expires_at <= 0:
        return False
    return _now_ms() + REFRESH_SKEW_MS >= int(expires_at)


def get_access_token() -> str:
    """
    返回一个可用账号的 access_token，必要时自动刷新。

    多账号时：优先未冷却的账号，轮转使用；当前账号刷新失败会切到下一个。

    Raises:
        AntigravityAuthError: 未登录、刷新失败或凭据被撤销
    """
    global _active_account_id
    with _lock:
        tried = set()
        while True:
            credentials = pick_account(CREDENTIALS_FILE, prefer_id=None, exclude=tried)
            if credentials is None:
                all_accounts = read_pool(CREDENTIALS_FILE)
                if all_accounts:
                    raise AntigravityAuthError(
                        "所有 Google 账号都不可用（配额冷却或凭据失效）。\n"
                        "Giải pháp: đăng nhập thêm tài khoản Google khác ở trang Cài đặt "
                        "hoặc chờ cửa sổ 配额复位 rồi quay lại."
                    )
                raise AntigravityAuthError(
                    "尚未登录 tài khoản Google.\n"
                    "Giải pháp: Hoàn tất đăng nhập trong phần 'Đăng nhập Google' tại trang Cài đặt hệ thống (không cần API Key)."
                )

            _active_account_id = credentials['id']
            if not needs_refresh(credentials):
                touch_account(CREDENTIALS_FILE, credentials['id'])
                return credentials['token']

            try:
                token = _refresh_locked(credentials)
                touch_account(CREDENTIALS_FILE, credentials['id'])
                return token
            except AntigravityAuthError:
                tried.add(credentials['id'])
                remaining = healthy_accounts(CREDENTIALS_FILE, exclude=list(tried))
                if not remaining:
                    raise
                logger.warning(
                    f"Antigravity 账号 {credentials.get('email') or credentials['id']} 刷新失败，切换到下一个账号"
                )
                _active_account_id = None
                continue


def force_refresh() -> str:
    """上游返回 401 时强制刷新当前账号一次。"""
    global _active_account_id
    with _lock:
        credentials = pick_account(CREDENTIALS_FILE, prefer_id=_active_account_id)
        if credentials is None:
            raise AntigravityAuthError("尚未登录 tài khoản Google, vui lòng đăng nhập trước trong trang Cài đặt hệ thống.")
        return _refresh_locked(credentials, ignore_expiry=True)


def _refresh_locked(credentials: dict, ignore_expiry: bool = False) -> str:
    with _lock:
        # 双重检查：另一个线程可能已经刷新完成
        current = _account_by_id(credentials['id']) or credentials
        if not ignore_expiry and not needs_refresh(current):
            return current['token']

        refresh_token = current.get('refresh_token')
        if not refresh_token:
            raise AntigravityAuthError(
                "Thông tin xác thực đăng nhập đã hết hạn (thiếu refresh_token), vui lòng 重新登录 Google."
            )

        logger.info("刷新 Antigravity OAuth access_token...")
        try:
            response = requests.post(
                TOKEN_URL,
                data={
                    'grant_type': 'refresh_token',
                    'client_id': OAUTH_CLIENT_ID,
                    'client_secret': OAUTH_CLIENT_SECRET,
                    'refresh_token': refresh_token,
                },
                headers={'User-Agent': antigravity_user_agent()},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise AntigravityAuthError(f"Lỗi 网络 khi làm mới thông tin xác thực Google: {exc}")

        if response.status_code != 200:
            detail = response.text[:300]
            if response.status_code in (400, 401):
                # invalid_grant：用户撤销授权或凭据被轮换，必须重新登录
                _drop_account(current['id'])
                raise AntigravityAuthError(
                    "Đăng nhập Google đã hết hạn, cần 重新登录.\n"
                    f"【上游响应】{detail}"
                )
            raise AntigravityAuthError(f"Làm mới thông tin xác thực đăng nhập thất bại (HTTP {response.status_code}): {detail}")

        payload = response.json()
        access_token = payload.get('access_token')
        if not access_token:
            raise AntigravityAuthError(f"Phản hồi làm mới của Google thiếu access_token: {str(payload)[:200]}")

        expires_in = int(payload.get('expires_in') or 3599)
        expires_at = _now_ms() + expires_in * 1000 - CREDENTIAL_SKEW_MS

        save_credentials(
            token=access_token,
            refresh_token=payload.get('refresh_token') or current.get('refresh_token'),
            expires_at=expires_at,
            project_id=current.get('project_id'),
            email=current.get('email'),
            account_id=current['id'],
        )
        logger.info("Antigravity access_token 刷新成功")
        return access_token


def _account_by_id(account_id: str) -> Optional[dict]:
    for account in read_pool(CREDENTIALS_FILE):
        if account['id'] == account_id:
            return account
    return None


def _drop_account(account_id: str) -> None:
    """invalid_grant：池里只剩它就直接清空文件，否则标记失效。"""
    global _active_account_id
    with _lock:
        accounts = read_pool(CREDENTIALS_FILE)
        if len(accounts) <= 1:
            clear_pool(CREDENTIALS_FILE)
            _invalidate_dependents()
        else:
            mark_dead(CREDENTIALS_FILE, account_id, reason='invalid_grant')
        if _active_account_id == account_id:
            _active_account_id = None


def get_project_id(access_token: str = None) -> str:
    """返回当前账号登录时发现的 Cloud Code Assist project id。"""
    credentials = load_credentials()
    if not credentials:
        raise AntigravityAuthError("尚未登录 tài khoản Google, vui lòng đăng nhập trước trong trang Cài đặt hệ thống.")
    project_id = credentials.get('project_id')
    if not project_id:
        raise AntigravityAuthError(
            "Thông tin xác thực đăng nhập thiếu project id, vui lòng 重新登录 tài khoản Google."
        )
    return project_id


def fetch_account_email(access_token: str) -> Optional[str]:
    try:
        response = requests.get(
            USERINFO_URL,
            headers={'Authorization': f"Bearer {access_token}"},
            timeout=15,
        )
        if response.status_code == 200:
            return response.json().get('email')
    except (requests.RequestException, ValueError) as exc:
        logger.debug(f"获取 Google 账号邮箱失败: {exc}")
    return None


def connection_summary() -> dict:
    """
    给前端展示的连接状态（不包含 token 本身）。

    单账号字段保持旧版形状；额外带上账号池视图，前端可以列出多个账号。
    """
    summary = pool_summary(CREDENTIALS_FILE)
    credentials = load_credentials()

    if not credentials:
        return {
            'connected': False,
            'email': None,
            'project_id': None,
            'expires_at': None,
            'expires_in_seconds': None,
            'needs_relogin': False,
            'account_count': summary['account_count'],
            'healthy_count': 0,
            'cooling_down_count': summary['cooling_down_count'],
            'dead_count': summary['dead_count'],
            'accounts': summary['accounts'],
            'recovered_at': summary['recovered_at'],
        }

    expires_at = credentials.get('expires_at')
    expires_in = None
    if isinstance(expires_at, (int, float)) and expires_at > 0:
        expires_in = max(0, int((int(expires_at) - _now_ms()) / 1000))

    return {
        'connected': True,
        'email': credentials.get('email'),
        'project_id': credentials.get('project_id'),
        'account_id': credentials.get('id'),
        'expires_at': expires_at,
        'expires_in_seconds': expires_in,
        'can_refresh': bool(credentials.get('refresh_token')),
        'needs_relogin': not bool(credentials.get('refresh_token')) and needs_refresh(credentials),
        'account_count': summary['account_count'],
        'healthy_count': summary['healthy_count'],
        'cooling_down_count': summary['cooling_down_count'],
        'dead_count': summary['dead_count'],
        'accounts': summary['accounts'],
        'recovered_at': summary['recovered_at'],
    }


def account_email(account_id: str) -> Optional[str]:
    for account in read_pool(CREDENTIALS_FILE):
        if account['id'] == account_id:
            return account.get('email')
    return None


def _invalidate_dependents():
    try:
        from backend.config import Config
        Config._image_providers_config = None
        Config._text_providers_config = None
    except Exception:
        pass
    try:
        from backend.services.image import reset_image_service
        reset_image_service()
    except Exception:
        pass
    try:
        from ..utils.antigravity_text_client import reset_antigravity_text_clients
        reset_antigravity_text_clients()
    except Exception:
        pass


__all__ = [
    'AntigravityAuthError',
    'CREDENTIALS_FILE',
    'account_email',
    'account_ids',
    'account_list',
    'clear_account',
    'clear_credentials',
    'connection_summary',
    'current_account_id',
    'fetch_account_email',
    'force_refresh',
    'get_access_token',
    'get_project_id',
    'is_blocked',
    'load_credentials',
    'needs_refresh',
    'note_credentials_dead',
    'note_quota_exhausted',
    'note_refresh_failed',
    'rotation_available',
    'save_credentials',
    'unblock_account',
]
