"""
Antigravity（Cloud Code Assist）OAuth 登录 + 项目发现

流程与 oh-my-pi 的 google-antigravity 登录完全一致：
1. 本地 loopback HTTP 服务（127.0.0.1:51121）等待 Google 回调
2. 授权码 + PKCE 换取 access_token / refresh_token
3. loadCodeAssist → 必要时 onboardUser（免费档开通）→ 轮询 LRO
4. 取到 cloudaicompanionProject，落盘凭据

登录在后台线程中执行，前端通过 /api/auth/antigravity/status 轮询状态。
"""
import logging
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .antigravity_constants import (
    ANTIGRAVITY_METADATA,
    AUTHORIZE_URL,
    CALLBACK_HOSTNAME,
    CALLBACK_PATH,
    CALLBACK_PORT,
    DAILY_ENDPOINT,
    FREE_TIER_ID,
    LOGIN_TIMEOUT_SECONDS,
    OAUTH_CLIENT_ID,
    OAUTH_CLIENT_SECRET,
    ONBOARD_POLL_INTERVAL_SECONDS,
    ONBOARD_TIMEOUT_SECONDS,
    OAUTH_SCOPES,
    TOKEN_URL,
    USERINFO_URL,
    antigravity_user_agent,
    sha256_b64url,
)
from .antigravity_credentials import (
    AntigravityAuthError,
    _now_ms,
    fetch_account_email,
    load_credentials,
    save_credentials,
)

logger = logging.getLogger(__name__)

STATE_UNVERIFIABLE = object()


class LoginAborted(Exception):
    """登录被取消或超时。"""


class _CallbackResult:
    __slots__ = ('code', 'state', 'error')

    def __init__(self, code=None, state=None, error=None):
        self.code = code
        self.state = state
        self.error = error


class AntigravityLoginManager:
    """单例登录管理器：同一时间只允许一个进行中的登录。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._thread = None
        self._server = None
        self._redirect_uri = None
        self._state_expected = STATE_UNVERIFIABLE
        self._result = _CallbackResult()
        self._status = {'state': 'idle', 'message': '', 'auth_url': None, 'error': None}
        self._cancel_requested = False

    # ---------- 状态 ----------

    def status(self) -> dict:
        with self._lock:
            status = dict(self._status)
            status['redirect_uri'] = self._redirect_uri
            return status

    def auth_url(self) -> Optional[str]:
        with self._lock:
            return self._status.get('auth_url')

    # ---------- 启动 / 取消 ----------

    def start(self, preferred_port: int = None) -> dict:
        """启动登录：绑定本地回调端口并生成授权链接，然后后台完成交换。"""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.status()
            if not OAUTH_CLIENT_ID or not OAUTH_CLIENT_SECRET:
                raise AntigravityAuthError(
                    "Thiếu thông tin OAuth của Antigravity (client_id / client_secret).\n"
                    "Giải pháp: đặt biến môi trường REDINK_ANTIGRAVITY_CLIENT_ID và "
                    "REDINK_ANTIGRAVITY_CLIENT_SECRET, hoặc tạo tệp .antigravity_client.json "
                    "{\"client_id\": ..., \"client_secret\": ...} ở thư mục gốc dự án."
                )

            port = CALLBACK_PORT if preferred_port is None else preferred_port
            self._result = _CallbackResult()
            self._cancel_requested = False
            self._state_expected = STATE_UNVERIFIABLE

            try:
                self._server, self._redirect_uri = self._bind_callback_server(port)
            except OSError as exc:
                self._set_status('failed', error=f"无法监听本地回调端口 {port}: {exc}")
                raise AntigravityAuthError(
                    f"Không thể khởi động dịch vụ callback đăng nhập tại 127.0.0.1:{port}.\n"
                    "Giải pháp: Đóng ứng dụng đang chiếm cổng này, hoặc đặt biến môi trường REDINK_ANTIGRAVITY_PORT để đổi sang cổng khác."
                )

            auth_url, state, verifier = self._build_auth_url(self._redirect_uri)
            self._state_expected = state
            self._set_status('waiting', message='请在浏览器中完成 Google 登录', auth_url=auth_url)
            logger.info(f"Antigravity 登录回调已就绪: {self._redirect_uri}")

            self._thread = threading.Thread(
                target=self._run_flow,
                args=(auth_url, verifier, state),
                name='antigravity-oauth-login',
                daemon=True,
            )
            self._thread.start()
            return self.status()

    def cancel(self):
        with self._lock:
            self._cancel_requested = True
            self._close_server()
            if self._status.get('state') in ('waiting', 'provisioning'):
                self._set_status('cancelled', message='登录已取消')

    # ---------- 后台流程 ----------

    def _run_flow(self, auth_url: str, verifier: str, state: str):
        try:
            self._open_browser(auth_url)
            result = self._wait_for_callback()
            if result.error:
                raise AntigravityAuthError(f"Ủy quyền Google thất bại: {result.error}")

            self._set_status('provisioning', message='正在换取 Google 凭据...')
            tokens = self._exchange_code(result.code, verifier)
            access_token = tokens.get('access_token')
            refresh_token = tokens.get('refresh_token')
            if not access_token:
                raise AntigravityAuthError("Google không trả về access_token, vui lòng thử đăng nhập lại.")
            if not refresh_token:
                raise AntigravityAuthError(
                    "Google không trả về refresh_token (có thể do đã bỏ qua trang cấp quyền khi đăng nhập lại).\n"
                    "Giải pháp: Trong tài khoản Google, vào mục 'Bảo mật → Quyền truy cập của ứng dụng bên thứ ba' để xoá quyền của ứng dụng này rồi thử lại."
                )

            expires_in = int(tokens.get('expires_in') or 3599)
            expires_at = _now_ms() + expires_in * 1000 - 300_000

            self._set_status('provisioning', message='正在开通 Cloud Code Assist 免费档...')
            project_id = discover_project(access_token)

            email = fetch_account_email(access_token)
            save_credentials(
                token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
                project_id=project_id,
                email=email,
            )
            self._set_status(
                'connected',
                message=f"登录成功：{email or 'Google 账号'}（project: {project_id}）",
            )
            logger.info(f"Antigravity 登录成功: email={email} project={project_id}")
        except (AntigravityAuthError, LoginAborted) as exc:
            self._set_status('failed' if not isinstance(exc, LoginAborted) else 'cancelled',
                             error=str(exc),
                             message=str(exc))
            logger.error(f"Antigravity 登录失败: {exc}")
        except Exception as exc:  # 兜底：任何异常都要让前端看到终态
            self._set_status('failed', error=str(exc), message=str(exc))
            logger.exception("Antigravity 登录出现未预期错误")
        finally:
            with self._lock:
                self._close_server()

    def _wait_for_callback(self) -> _CallbackResult:
        deadline = time.time() + LOGIN_TIMEOUT_SECONDS
        with self._lock:
            server = self._server
        while time.time() < deadline:
            with self._lock:
                if self._cancel_requested:
                    raise LoginAborted("Đăng nhập đã bị hủy")
                result = self._result
            if result.code or result.error:
                return result
            if server is not None:
                server.handle_request()
            time.sleep(0.1)
        raise LoginAborted(f"Đăng nhập quá thời gian (không hoàn tất ủy quyền trên trình duyệt trong {LOGIN_TIMEOUT_SECONDS}s)")

    # ---------- 本地回调服务 ----------

    def _bind_callback_server(self, port: int):
        manager = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.0'

            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path.rstrip('/') != CALLBACK_PATH.rstrip('/'):
                    self._respond(404, '红墨登录：未知路径')
                    return
                params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
                error = params.get('error')
                returned_state = params.get('state')

                if error:
                    outcome = _CallbackResult(error=params.get('error_description') or error)
                elif not params.get('code'):
                    outcome = _CallbackResult(error='回调中没有 authorization code')
                else:
                    outcome = _CallbackResult(code=params['code'], state=returned_state)

                manager._record_callback(outcome)
                self._respond(200, '登录完成，可以关闭此页面并回到红墨。')

            def log_message(self, fmt, *args):
                logger.debug(f"Antigravity 回调服务: {fmt % args}")

            def _respond(self, status: int, text: str):
                body = f"<html><body style='font-family:sans-serif'>{text}</body></html>".encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = HTTPServer((CALLBACK_HOSTNAME, port), Handler)
        server.timeout = 0.1
        actual_port = server.server_address[1]
        return server, f"http://{CALLBACK_HOSTNAME}:{actual_port}{CALLBACK_PATH}"

    def _record_callback(self, outcome: _CallbackResult):
        with self._lock:
            if self._state_expected is not STATE_UNVERIFIABLE and outcome.state not in (None, self._state_expected):
                self._result = _CallbackResult(error='state 校验失败（可能存在 CSRF），请重试登录')
            else:
                self._result = outcome

    def _close_server(self):
        if self._server is not None:
            try:
                self._server.server_close()
            except OSError:
                pass
            self._server = None

    # ---------- OAuth 交换 ----------

    def _build_auth_url(self, redirect_uri: str):
        verifier = secrets.token_urlsafe(96)[:128]
        challenge = sha256_b64url(verifier)
        state = secrets.token_hex(16)
        params = {
            'client_id': OAUTH_CLIENT_ID,
            'response_type': 'code',
            'redirect_uri': redirect_uri,
            'scope': ' '.join(OAUTH_SCOPES),
            'code_challenge': challenge,
            'code_challenge_method': 'S256',
            'state': state,
            'access_type': 'offline',
            'prompt': 'consent',
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}", state, verifier

    def _exchange_code(self, code: str, verifier: str) -> dict:
        response = requests.post(
            TOKEN_URL,
            data={
                'grant_type': 'authorization_code',
                'client_id': OAUTH_CLIENT_ID,
                'client_secret': OAUTH_CLIENT_SECRET,
                'code': code,
                'redirect_uri': self._redirect_uri,
                'code_verifier': verifier,
            },
            headers={'User-Agent': antigravity_user_agent()},
            timeout=30,
        )
        if response.status_code != 200:
            raise AntigravityAuthError(
                f"Trao đổi mã ủy quyền thất bại (HTTP {response.status_code}): {response.text[:300]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise AntigravityAuthError(f"Phản hồi token từ Google không phải JSON: {response.text[:200]}") from exc

    @staticmethod
    def _open_browser(auth_url: str):
        try:
            import webbrowser

            webbrowser.open(auth_url)
        except Exception as exc:
            logger.warning(f"无法自动打开浏览器，请手动访问授权链接: {exc}")

    def _set_status(self, state: str, message: str = None, error: str = None, auth_url: str = None):
        with self._lock:
            self._status = {
                'state': state,
                'message': message or self._status.get('message', ''),
                'auth_url': auth_url or self._status.get('auth_url'),
                'error': error,
            }


# ==================== 项目发现 / 开通 ====================

def _cloud_code_headers(access_token: str) -> dict:
    return {
        'Authorization': f"Bearer {access_token}",
        'Content-Type': 'application/json',
        'User-Agent': antigravity_user_agent(),
    }


def _post_cloud_code(access_token: str, path: str, payload: dict, timeout: int = 30) -> dict:
    url = f"{DAILY_ENDPOINT}{path}"
    try:
        response = requests.post(url, json=payload, headers=_cloud_code_headers(access_token), timeout=timeout)
    except requests.RequestException as exc:
        raise AntigravityAuthError(f"Yêu cầu Cloud Code Assist thất bại ({path}): {exc}")
    if response.status_code != 200:
        raise AntigravityAuthError(
            f"Cloud Code Assist {path} thất bại: HTTP {response.status_code} {response.text[:300]}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise AntigravityAuthError(f"Phản hồi Cloud Code Assist {path} không phải JSON") from exc


def _get_cloud_code(access_token: str, path: str, timeout: int = 30) -> dict:
    url = f"{DAILY_ENDPOINT}{path}"
    try:
        response = requests.get(url, headers=_cloud_code_headers(access_token), timeout=timeout)
    except requests.RequestException as exc:
        raise AntigravityAuthError(f"Yêu cầu Cloud Code Assist thất bại ({path}): {exc}")
    if response.status_code != 200:
        raise AntigravityAuthError(
            f"Cloud Code Assist {path} thất bại: HTTP {response.status_code} {response.text[:300]}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise AntigravityAuthError(f"Phản hồi Cloud Code Assist {path} không phải JSON") from exc


def load_code_assist(access_token: str, project_id: str = None) -> dict:
    body = {'metadata': ANTIGRAVITY_METADATA}
    if project_id:
        body['cloudaicompanionProject'] = project_id
    payload = _post_cloud_code(access_token, '/v1internal:loadCodeAssist', body)

    initial_project = payload.get('cloudaicompanionProject')
    # 只有带 project 再问一次，服务端才会回填 tier 信息
    if 'paidTier' not in payload and initial_project:
        payload = _post_cloud_code(access_token, '/v1internal:loadCodeAssist',
                                   {'cloudaicompanionProject': initial_project,
                                    'metadata': ANTIGRAVITY_METADATA})
    return payload


def assert_free_tier_eligible(payload: dict):
    allowed = [tier.get('id') for tier in payload.get('allowedTiers') or []]
    if FREE_TIER_ID in allowed:
        return
    ineligible = next((t for t in payload.get('ineligibleTiers') or [] if t.get('tierId') == FREE_TIER_ID), None)
    if not ineligible:
        return
    reason = ineligible.get('reasonMessage') or 'Tài khoản Google này không đủ điều kiện dùng gói miễn phí của Antigravity'
    validation_url = ineligible.get('validationUrl')
    hint = f"\nVui lòng hoàn tất xác minh tài khoản Google trước: {validation_url}" if validation_url else ''
    raise AntigravityAuthError(f"Tài khoản Antigravity không khả dụng: {reason}{hint}")


def onboard_user(access_token: str):
    """开通免费档，并轮询 LRO 直到完成（对齐 ONBOARD_TIMEOUT_MS=30s）。"""
    deadline = time.time() + ONBOARD_TIMEOUT_SECONDS
    remaining = lambda: max(0.1, deadline - time.time())  # noqa: E731

    operation = _post_cloud_code(
        access_token,
        '/v1internal:onboardUser',
        {'tierId': FREE_TIER_ID, 'metadata': ANTIGRAVITY_METADATA},
        timeout=int(remaining()),
    )

    while True:
        if operation.get('done') is True:
            error = operation.get('error')
            if error:
                message = error.get('message') or str(error)
                code = error.get('code')
                raise AntigravityAuthError(f"Kích hoạt gói miễn phí Antigravity thất bại: {code}: {message}" if code else f"Kích hoạt thất bại: {message}")
            if not operation.get('response'):
                raise AntigravityAuthError("Kích hoạt gói miễn phí Antigravity thất bại: Phản hồi thiếu result")
            return
        time.sleep(min(ONBOARD_POLL_INTERVAL_SECONDS, remaining()))
        name = operation.get('name') or ''
        if not name:
            raise AntigravityAuthError("Kích hoạt gói miễn phí Antigravity thất bại: Thao tác không có name")
        operation = _get_cloud_code(access_token, f"/v1internal/{name}", timeout=int(remaining()))


def discover_project(access_token: str) -> str:
    """loadCodeAssist → 必要时 onboardUser → 重新 loadCodeAssist 拿 project id。"""
    initial = load_code_assist(access_token)
    assert_free_tier_eligible(initial)
    if not initial.get('currentTier'):
        logger.info("账号尚未开通 Antigravity 免费档，正在开通...")
        onboard_user(access_token)

    refreshed = load_code_assist(access_token)
    project_id = refreshed.get('cloudaicompanionProject')
    if not project_id:
        raise AntigravityAuthError(
            "Không thể lấy được project id của Cloud Code Assist.\n"
            "Giải pháp: Xác nhận khu vực của tài khoản Google này được phép dùng Antigravity, sau đó đăng nhập lại."
        )
    return project_id


_login_manager = AntigravityLoginManager()


def get_login_manager() -> AntigravityLoginManager:
    return _login_manager


__all__ = [
    'AntigravityLoginManager',
    'discover_project',
    'get_login_manager',
    'load_code_assist',
    'onboard_user',
]
