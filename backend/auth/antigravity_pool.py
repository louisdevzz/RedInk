"""
Antigravity 多账号凭据池（Google 账号登录 / Cloud Code Assist）

参考 oh-my-pi auth-broker 的账号池思路：每个 Google 账号是一条独立凭据记录，
各自持有 access/refresh token、project id 与"配额冷却"状态。当上游对某个账号
返回 429（含 quotaResetTimeStamp）或 401 且刷新失败时，该账号被临时/永久拉黑，
请求自动切换到下一个健康账号，而不是原地退避重试到超时。

存储：沿用单账号时期的同一个文件（.antigravity_credentials.json），
内容升级为 {"version": 2, "accounts": [...]}；读到旧版单账号格式会自动迁移。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

POOL_VERSION = 2

# 没有显式 reset 时间时，429 的默认冷却窗口
DEFAULT_QUOTA_COOLDOWN_MS = 60_000
# 刷新失败但尚未确认被撤销时的短冷却，避免每个请求都撞同一个坏账号
DEFAULT_TRANSIENT_COOLDOWN_MS = 30_000

_lock = threading.RLock()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _seconds_left(expires_at, now: int) -> Optional[int]:
    if not isinstance(expires_at, (int, float)) or expires_at <= 0:
        return None
    return max(0, (int(expires_at) - now) // 1000)


# ==================== 账号 id ====================

def account_id_for(email: Optional[str] = None, project_id: Optional[str] = None,
                   refresh_token: Optional[str] = None) -> str:
    """账号的稳定身份：优先 email，其次 project id，最后 refresh_token 哈希。"""
    base = (email or '').strip().lower() or (project_id or '').strip()
    if not base:
        digest = hashlib.sha1((refresh_token or '').encode('utf-8')).hexdigest()
        base = f'acct-{digest[:12]}'
    return base


# ==================== 读写 ====================

def _normalize_account(record: dict) -> dict:
    return {
        'id': record.get('id') or account_id_for(record.get('email'), record.get('project_id'),
                                                 record.get('refresh_token')),
        'token': record.get('token'),
        'refresh_token': record.get('refresh_token'),
        'expires_at': record.get('expires_at'),
        'project_id': record.get('project_id'),
        'email': record.get('email'),
        'updated_at': record.get('updated_at'),
        'blocked_until': record.get('blocked_until'),
        'block_reason': record.get('block_reason'),
        'dead': bool(record.get('dead')),
        'last_used': record.get('last_used'),
        'last_error': record.get('last_error'),
    }


def _migrate_legacy(data: dict) -> List[dict]:
    """旧版单账号格式 -> 账号列表。"""
    if not isinstance(data, dict) or not data.get('token'):
        return []
    return [_normalize_account({
        'id': account_id_for(data.get('email'), data.get('project_id'), data.get('refresh_token')),
        'token': data.get('token'),
        'refresh_token': data.get('refresh_token'),
        'expires_at': data.get('expires_at'),
        'project_id': data.get('project_id'),
        'email': data.get('email'),
        'updated_at': data.get('updated_at'),
    })]


def read_pool(path: Path) -> List[dict]:
    """读取账号池；文件不存在或损坏时返回空列表。"""
    with _lock:
        if not path.exists():
            return []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error(f"读取 Antigravity 账号池失败: {exc}")
            return []

        if not isinstance(data, dict):
            return []
        if data.get('version') == POOL_VERSION and isinstance(data.get('accounts'), list):
            return [_normalize_account(a) for a in data['accounts'] if isinstance(a, dict) and a.get('token')]
        return _migrate_legacy(data)


def write_pool(path: Path, accounts: List[dict]) -> None:
    """原子写入账号池。"""
    with _lock:
        payload = {
            'version': POOL_VERSION,
            'updated_at': _now_ms(),
            'accounts': [_normalize_account(a) for a in accounts if a and a.get('token')],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix('.tmp')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # Windows 上 chmod 能力有限，忽略


def list_accounts(path: Path) -> List[dict]:
    """全部账号（含已拉黑/已失效），用于设置页展示。"""
    return read_pool(path)


def account_ids(path: Path) -> List[str]:
    return [a['id'] for a in read_pool(path)]


def clear_pool(path: Path) -> None:
    """退出全部账号：删除凭据文件。"""
    with _lock:
        if path.exists():
            try:
                path.unlink()
            except OSError as exc:
                logger.error(f"删除 Antigravity 账号池失败: {exc}")
        lock_path = path.with_suffix(path.suffix + '.lock')
        if lock_path.exists():
            try:
                lock_path.unlink()
            except OSError:
                pass


# ==================== 变更 ====================

def upsert_account(path: Path, token: str, refresh_token: str = None, expires_at: int = None,
                   project_id: str = None, email: str = None, account_id: str = None) -> dict:
    """登录 / 刷新后写入账号：同 id 覆盖，否则追加（多账号共存）。"""
    with _lock:
        accounts = read_pool(path)
        target_id = account_id or account_id_for(email, project_id, refresh_token)

        existing = next((a for a in accounts if a['id'] == target_id), None)
        if existing is None and project_id:
            # 同一 Google 账号但 email 缺失时，按 project id 兜底匹配
            existing = next((a for a in accounts if a.get('project_id') == project_id), None)
            if existing is not None:
                target_id = existing['id']

        record = {
            'id': target_id,
            'token': token,
            'refresh_token': refresh_token or (existing or {}).get('refresh_token'),
            'expires_at': expires_at or (existing or {}).get('expires_at'),
            'project_id': project_id or (existing or {}).get('project_id'),
            'email': email or (existing or {}).get('email'),
            'updated_at': _now_ms(),
            # 新 token 到手即解除配额冷却
            'blocked_until': None,
            'block_reason': None,
            'dead': False,
            'last_used': (existing or {}).get('last_used'),
            'last_error': None,
        }

        if existing is not None:
            accounts = [record if a['id'] == existing['id'] else a for a in accounts]
        else:
            accounts.append(record)

        write_pool(path, accounts)
        return record


def remove_account(path: Path, account_id: str) -> bool:
    """删除单个账号（退出该账号登录）。"""
    with _lock:
        accounts = read_pool(path)
        kept = [a for a in accounts if a['id'] != account_id]
        if len(kept) == len(accounts):
            return False
        if not kept:
            clear_pool(path)
            return True
        write_pool(path, kept)
        return True


def _patch_account(path: Path, account_id: str, changes: dict) -> None:
    accounts = read_pool(path)
    changed = False
    for account in accounts:
        if account['id'] != account_id:
            continue
        account.update(changes)
        changed = True
        break
    if changed:
        write_pool(path, accounts)


def mark_blocked(path: Path, account_id: str, until_ms: int, reason: str = None) -> None:
    """临时冷却：到期前不再选这个账号。"""
    with _lock:
        _patch_account(path, account_id, {
            'blocked_until': max(int(until_ms), _now_ms()),
            'block_reason': (reason or '')[:400] or None,
            'last_error': (reason or '')[:400] or None,
        })


def mark_dead(path: Path, account_id: str, reason: str = None) -> None:
    """凭据被撤销 / 无法刷新：永久跳过，直到用户重新登录该账号。"""
    with _lock:
        _patch_account(path, account_id, {
            'dead': True,
            'blocked_until': None,
            'last_error': (reason or '')[:400] or None,
        })


def clear_block(path: Path, account_id: str) -> None:
    with _lock:
        _patch_account(path, account_id, {'blocked_until': None, 'block_reason': None})


def touch_account(path: Path, account_id: str) -> None:
    """记录使用顺序，保证多账号轮转公平。"""
    with _lock:
        _patch_account(path, account_id, {'last_used': _now_ms()})


# ==================== 选择 ====================

def is_blocked(account: dict, now: int = None) -> bool:
    if account.get('dead'):
        return True
    blocked_until = account.get('blocked_until')
    if not blocked_until:
        return False
    return int(blocked_until) > (now if now is not None else _now_ms())


def _usable(accounts: List[dict], exclude: Iterable[str], now: int) -> List[dict]:
    skip = set(exclude or ())
    usable = [a for a in accounts if a.get('token') and a['id'] not in skip and not is_blocked(a, now)]
    usable.sort(key=lambda a: (a.get('last_used') or 0, a.get('updated_at') or 0))
    return usable


def healthy_accounts(path: Path, exclude: Iterable[str] = ()) -> List[dict]:
    """可用账号：未失效、未冷却；按最久未使用排序（轮转）。"""
    return _usable(read_pool(path), exclude, _now_ms())


def pick_account(path: Path, prefer_id: str = None, exclude: Iterable[str] = ()) -> Optional[dict]:
    """选下一个账号；prefer_id 健康时优先（保持会话粘性），否则轮转。"""
    now = _now_ms()
    accounts = read_pool(path)
    skip = set(exclude or ())

    if prefer_id and prefer_id not in skip:
        preferred = next((a for a in accounts if a['id'] == prefer_id), None)
        if preferred and preferred.get('token') and not is_blocked(preferred, now):
            return preferred

    usable = _usable(accounts, skip, now)
    return usable[0] if usable else None


def next_account(path: Path, exclude: Iterable[str]) -> Optional[dict]:
    """轮换用的显式入口：排除已尝试过的账号后取下一个。"""
    return pick_account(path, exclude=exclude)


def blocked_until_any(path: Path) -> Optional[int]:
    """全部账号都在冷却时，最早恢复的时间戳（用于友好提示）。"""
    now = _now_ms()
    upcoming = [int(a['blocked_until']) for a in read_pool(path)
                if a.get('blocked_until') and int(a['blocked_until']) > now and not a.get('dead')]
    return min(upcoming) if upcoming else None


# ==================== 展示 ====================

def account_summary(account: dict, now: int = None) -> dict:
    """给前端的单账号状态（不含 token）。"""
    now = now if now is not None else _now_ms()
    blocked_until = account.get('blocked_until')
    return {
        'id': account['id'],
        'email': account.get('email'),
        'project_id': account.get('project_id'),
        'connected': bool(account.get('token')) and not account.get('dead'),
        'dead': bool(account.get('dead')),
        'cooling_down': bool(blocked_until) and int(blocked_until) > now,
        'blocked_until': blocked_until,
        'block_reason': account.get('block_reason'),
        'last_error': account.get('last_error'),
        'can_refresh': bool(account.get('refresh_token')),
        'expires_at': account.get('expires_at'),
        'expires_in_seconds': _seconds_left(account.get('expires_at'), now),
        'last_used': account.get('last_used'),
        'updated_at': account.get('updated_at'),
    }


def pool_summary(path: Path) -> dict:
    """账号池整体状态（设置页 + 启动自检用）。"""
    now = _now_ms()
    summaries = [account_summary(a, now) for a in read_pool(path)]
    healthy = [s for s in summaries if s['connected'] and not s['cooling_down'] and not s['dead']]
    return {
        'connected': bool(healthy),
        'account_count': len(summaries),
        'healthy_count': len(healthy),
        'cooling_down_count': len([s for s in summaries if s['cooling_down']]),
        'dead_count': len([s for s in summaries if s['dead']]),
        'accounts': summaries,
        'active': healthy[0] if healthy else None,
        'recovered_at': blocked_until_any(path),
    }


# ==================== 配额 / 错误解析 ====================

_QUOTA_TOKENS = (
    'resource_exhausted',
    'quota',
    'rate limit',
    'ratelimit',
    'too many requests',
    'exhausted',
    '限流',
    '配额',
    '用量上限',
)


def looks_like_quota_error(text: str, status_code: int = None) -> bool:
    """429 或文本里出现配额/限流关键词。"""
    if status_code == 429:
        return True
    lowered = (text or '').lower()
    if not lowered:
        return False
    if '429' in lowered and any(token in lowered for token in ('quota', 'rate', 'exhausted')):
        return True
    return any(token in lowered for token in _QUOTA_TOKENS)


_RESET_KEYS = ('quotaResetTimeStamp', 'quotaResetTime', 'retryDelay', 'retryDelayMs', 'resetTime')
_ISO_RE = re.compile(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?')
_DELAY_RE = re.compile(r'in\s+(\d+(?:\.\d+)?)\s*(s|sec|secs|seconds|min|mins|minutes)\b')


def parse_reset_time(payload, now: int = None, default_ms: int = DEFAULT_QUOTA_COOLDOWN_MS) -> int:
    """
    从上游错误体里解析恢复时间 -> epoch 毫秒。

    支持：ISO 字符串（含 Z / 时区偏移）、epoch 秒、epoch 毫秒、retryDelay("30s")。
    解析不到就返回 now + default_ms。
    """
    now = now if now is not None else _now_ms()

    if isinstance(payload, str):
        resolved = _reset_from_text(payload, now)
        return resolved or (now + default_ms)

    if isinstance(payload, dict):
        for key in _RESET_KEYS:
            if key in payload:
                resolved = _coerce_reset_ms(payload.get(key), now)
                if resolved:
                    return resolved
        for value in payload.values():
            if isinstance(value, dict):
                resolved = parse_reset_time(value, now, default_ms=0)
                if resolved and resolved != now:
                    return resolved
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        resolved = parse_reset_time(item, now, default_ms=0)
                        if resolved and resolved != now:
                            return resolved
        if default_ms:
            return now + default_ms
        return 0

    return now + default_ms


def _reset_from_text(text: str, now: int) -> Optional[int]:
    match = _ISO_RE.search(text or '')
    if match:
        resolved = _coerce_reset_ms(match.group(0), now)
        if resolved:
            return resolved
    delay = _DELAY_RE.search((text or '').lower())
    if delay:
        unit = delay.group(2)
        factor = 60_000 if unit.startswith('m') else 1_000
        return now + int(float(delay.group(1)) * factor)
    return None


def _coerce_reset_ms(value, now: int) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        stamp = int(value)
        if stamp <= 0:
            return None
        # 10 位是秒，13 位是毫秒
        return stamp * 1000 if stamp < 10_000_000_000 else stamp
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return _coerce_reset_ms(int(text), now)
        delay = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|seconds|m|min|mins|minutes|h|hr|hours)?',
                             text.lower())
        if delay:
            unit = delay.group(2) or 's'
            factor = {
                'ms': 1, 's': 1_000, 'sec': 1_000, 'secs': 1_000, 'seconds': 1_000,
                'm': 60_000, 'min': 60_000, 'mins': 60_000, 'minutes': 60_000,
                'h': 3_600_000, 'hr': 3_600_000, 'hours': 3_600_000,
            }[unit]
            return now + int(float(delay.group(1)) * factor)
        try:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            match = _ISO_RE.search(text)
            if not match:
                return None
            try:
                parsed = datetime.fromisoformat(match.group(0).replace('Z', '+00:00'))
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    return None
