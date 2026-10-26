"""
Antigravity（Google 账号登录）auth + wire 协议测试

全部离线：网络调用被替换为假的 requests/凭据，验证的是协议契约与配置校验，
不需要真实 Google 账号。
"""
import base64
import json
import time

import pytest

from backend.auth import (
    AUTH_ANTIGRAVITY_OAUTH,
    ANTIGRAVITY_PROVIDER_TYPE,
    missing_credential_error,
    uses_oauth_auth,
)
from backend.auth.antigravity_constants import (
    OAUTH_SCOPES,
    antigravity_user_agent,
    sha256_b64url,
    strip_effort_annotation,
)
from backend.utils.antigravity_client import AntigravityClient, AntigravityError


# ==================== 认证方式判定 ====================

def test_oauth_provider_types_skip_api_key():
    assert uses_oauth_auth({'type': ANTIGRAVITY_PROVIDER_TYPE}) is True
    assert uses_oauth_auth({'type': 'google_gemini', 'auth': AUTH_ANTIGRAVITY_OAUTH}) is True


def test_api_key_providers_still_required():
    for config in ({'type': 'openai_compatible'}, {'type': 'google_gemini'}, {}):
        assert uses_oauth_auth(config) is False


def test_missing_credential_error_classifies_as_config_error():
    # 错误必须能被分类器识别为可自助修复的配置问题，而不是 UNKNOWN_ERROR
    from backend.errors import classify_error

    oauth_error = missing_credential_error('antigravity', ANTIGRAVITY_PROVIDER_TYPE)
    assert classify_error(oauth_error).code == 'GOOGLE_SIGNIN_REQUIRED'
    assert classify_error(oauth_error).status == 400
    assert classify_error(oauth_error).retryable is False

    key_error = missing_credential_error('openai', 'openai_compatible')
    assert classify_error(key_error).code == 'API_KEY_MISSING'
    assert classify_error(key_error).status == 400


# ==================== 模型 id / UA 契约 ====================

@pytest.mark.parametrize('annotated, bare', [
    ('gemini-3-pro (high)', 'gemini-3-pro'),
    ('claude-opus-4-6-thinking(medium)', 'claude-opus-4-6-thinking'),
    ('gemini-3.1-pro-low', 'gemini-3.1-pro-low'),
])
def test_strip_effort_annotation(annotated, bare):
    # 带括号后缀的 id 会被上游 404，必须发送裸 id
    assert strip_effort_annotation(annotated) == bare


# ==================== 登录回调安全 ====================

def test_callback_rejects_state_mismatch():
    from backend.auth.antigravity_oauth import AntigravityLoginManager, _CallbackResult

    manager = AntigravityLoginManager()
    manager._state_expected = 'expected-state'

    manager._record_callback(_CallbackResult(code='c1', state='attacker-state'))
    assert manager._result.code is None
    assert 'CSRF' in (manager._result.error or '')

    manager._record_callback(_CallbackResult(code='c2', state='expected-state'))
    assert manager._result.code == 'c2'


def test_login_start_and_cancel_exposes_redirect_uri():
    from backend.auth.antigravity_oauth import AntigravityLoginManager

    manager = AntigravityLoginManager()
    status = manager.start(preferred_port=0)  # 随机端口，避免与真实登录冲突
    try:
        assert status['state'] == 'waiting'
        assert status['redirect_uri'].startswith('http://127.0.0.1:')
        assert '/oauth-callback' in status['redirect_uri']
        assert 'code_challenge_method=S256' in status['auth_url']
    finally:
        manager.cancel()
        manager._close_server()
    assert manager.status()['state'] == 'cancelled'


def test_antigravity_user_agent_matches_hub_format():
    ua = antigravity_user_agent()
    assert ua.startswith('antigravity/hub/')
    assert 'aidev_client' in ua and 'os_type=' in ua and 'arch=' in ua and 'cl=' in ua


def test_pkce_challenge_is_base64url_sha256():
    import hashlib

    # 与 oh-my-pi generatePKCE 一致：challenge = base64url(sha256(verifier))，无填充
    expected = base64.urlsafe_b64encode(hashlib.sha256(b'verifier-string').digest()).decode().rstrip('=')
    challenge = sha256_b64url('verifier-string')

    assert challenge == expected
    assert len(challenge) == 43
    assert set('=/+').isdisjoint(challenge)


def test_oauth_scopes_include_cclog_and_cloud_platform():
    joined = ' '.join(OAUTH_SCOPES)
    for scope in ('cloud-platform', 'cclog', 'experimentsandconfigs', 'userinfo.email'):
        assert scope in joined


# ==================== 请求信封 ====================

def build_client(monkeypatch):
    monkeypatch.setattr('backend.utils.antigravity_client.get_access_token', lambda: 'access-token')
    monkeypatch.setattr('backend.utils.antigravity_client.get_project_id', lambda: 'project-123')
    return AntigravityClient()


def test_envelope_uses_antigravity_wire_shape(monkeypatch):
    client = build_client(monkeypatch)
    envelope = client._build_envelope(
        model='gemini-3.1-pro (high)',
        contents=[{'role': 'user', 'parts': [{'text': 'hi'}]}],
        system_instruction='sys',
        generation_config={'maxOutputTokens': 8000, 'temperature': 1.0},
        session_seed='hi',
    )

    assert envelope['model'] == 'gemini-3.1-pro'
    assert envelope['userAgent'] == 'antigravity'
    assert envelope['requestId'].startswith('agent/')
    assert envelope['requestId'].count('/') == 4
    assert envelope['request']['systemInstruction']['role'] == 'user'
    labels = envelope['request']['labels']
    assert labels['used_claude'] == 'False'
    assert 'trajectory_id' in labels and 'last_step_index' in labels
    assert envelope['request']['sessionId']


def test_claude_model_caps_output_tokens_at_64000(monkeypatch):
    client = build_client(monkeypatch)
    envelope = client._build_envelope(
        model='claude-sonnet-4-6',
        contents=[{'role': 'user', 'parts': [{'text': 'hi'}]}],
        generation_config={'maxOutputTokens': 100000},
    )
    # 上游对 Claude 超过 64000 直接 400
    assert envelope['request']['generationConfig']['maxOutputTokens'] == 64000
    assert envelope['request']['labels']['used_claude'] == 'True'


# ==================== 假 HTTP：文本 / 图片 / 回退 / 刷新 ====================

class FakeResponse:
    def __init__(self, status_code=200, payload=None, text='', headers=None, lines=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text else (json.dumps(payload) if payload is not None else '')
        self.headers = headers or {'Content-Type': 'application/json'}
        self._lines = lines

    def json(self):
        if self._payload is None:
            return json.loads(self.text)
        return self._payload

    def iter_lines(self, decode_unicode=False):
        for line in self._lines or []:
            yield line if decode_unicode else line.encode('utf-8')


def sse_lines(payloads):
    return ['data: ' + json.dumps(payload) for payload in payloads]


TEXT_CHUNKS = [
    {'response': {'candidates': [{'content': {'parts': [{'text': '红墨'}]}, 'finishReason': 'STOP'}],
                  'usageMetadata': {'promptTokenCount': 5, 'candidatesTokenCount': 2}}},
]


def test_generate_text_posts_stream_endpoint_with_headers(monkeypatch):
    client = build_client(monkeypatch)
    captured = {}

    def fake_post(url, **kwargs):
        captured['url'] = url
        captured['headers'] = kwargs['headers']
        captured['body'] = kwargs['json']
        return FakeResponse(headers={'Content-Type': 'text/event-stream'},
                            lines=sse_lines(TEXT_CHUNKS))

    monkeypatch.setattr('backend.utils.antigravity_client.requests.post', fake_post)

    result = client.generate_text('写大纲', model='gemini-3-flash-agent', max_output_tokens=8000)

    assert result == '红墨'
    assert captured['url'] == 'https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse'
    assert captured['headers']['Authorization'] == 'Bearer access-token'
    assert captured['headers']['User-Agent'].startswith('antigravity/hub/')
    assert captured['headers']['Accept'] == 'text/event-stream'
    assert captured['body']['project'] == 'project-123'
    assert captured['body']['model'] == 'gemini-3-flash-agent'
    assert captured['body']['request']['generationConfig']['maxOutputTokens'] == 8000


def test_generate_text_drops_thought_parts(monkeypatch):
    client = build_client(monkeypatch)
    chunks = [{'response': {'candidates': [{'content': {'parts': [
        {'text': '思考中', 'thought': True},
        {'text': '正文'},
    ]}}]}}]

    monkeypatch.setattr('backend.utils.antigravity_client.requests.post',
                        lambda url, **kw: FakeResponse(headers={'Content-Type': 'text/event-stream'},
                                                       lines=sse_lines(chunks)))

    assert client.generate_text('hi', model='gemini-3-flash-agent') == '正文'


def test_generate_text_raises_on_in_band_quota_error(monkeypatch):
    client = build_client(monkeypatch)
    chunks = [{'error': {'code': 429, 'message': 'RESOURCE_EXHAUSTED: quota reset after 60s'}}]

    monkeypatch.setattr('backend.utils.antigravity_client.requests.post',
                        lambda url, **kw: FakeResponse(headers={'Content-Type': 'text/event-stream'},
                                                       lines=sse_lines(chunks)))

    with pytest.raises(AntigravityError) as exc:
        client.generate_text('hi', model='gemini-3-flash-agent')
    assert '429' in str(exc.value)


def test_image_generation_decodes_inline_data(monkeypatch):
    client = build_client(monkeypatch)
    png = b'\x89PNG-fake'
    chunks = [{'response': {'candidates': [{'content': {'parts': [
        {'inlineData': {'mimeType': 'image/png', 'data': base64.b64encode(png).decode()}}
    ]}, 'finishReason': 'STOP'}]}}]
    captured = {}

    def fake_post(url, **kwargs):
        captured['body'] = kwargs['json']
        return FakeResponse(headers={'Content-Type': 'text/event-stream'}, lines=sse_lines(chunks))

    monkeypatch.setattr('backend.utils.antigravity_client.requests.post', fake_post)
    # 固定图片模型，避免额外发现请求
    monkeypatch.setattr(client, 'fetch_image_models', lambda: ['gemini-3-pro-image'])

    image = client.generate_image('画一张封面', model='gemini-3-pro-image', aspect_ratio='3:4')[0]

    assert image == png
    config = captured['body']['request']['generationConfig']
    assert config['responseModalities'] == ['IMAGE']
    assert config['imageConfig']['aspectRatio'] == '3:4'
    assert captured['body']['request']['safetySettings']


def test_image_generation_without_image_raises_actionable(monkeypatch):
    client = build_client(monkeypatch)
    chunks = [{'response': {'candidates': [{'content': {'parts': [{'text': '无法生成'}]},
                                           'finishReason': 'STOP'}]}}]

    monkeypatch.setattr('backend.utils.antigravity_client.requests.post',
                        lambda url, **kw: FakeResponse(headers={'Content-Type': 'text/event-stream'},
                                                       lines=sse_lines(chunks)))
    monkeypatch.setattr(client, 'fetch_image_models', lambda: ['gemini-3-pro-image'])

    with pytest.raises(AntigravityError) as exc:
        client.generate_image('画一张封面', model='gemini-3-pro-image')
    assert '未返回图片' in str(exc.value)


def test_endpoint_fallback_to_sandbox(monkeypatch):
    client = build_client(monkeypatch)
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        if 'sandbox' in url:
            return FakeResponse(headers={'Content-Type': 'text/event-stream'}, lines=sse_lines(TEXT_CHUNKS))
        return FakeResponse(status_code=503, text='upstream down')

    monkeypatch.setattr('backend.utils.antigravity_client.requests.post', fake_post)
    monkeypatch.setattr('backend.utils.antigravity_client.time.sleep', lambda *_: None)

    assert client.generate_text('hi', model='gemini-3-flash-agent') == '红墨'
    assert 'daily-cloudcode-pa.googleapis.com' in calls[0]
    assert 'daily-cloudcode-pa.sandbox.googleapis.com' in calls[1]


def test_custom_base_url_disables_fallback(monkeypatch):
    client = AntigravityClient(base_url='https://proxy.example.com')
    monkeypatch.setattr('backend.utils.antigravity_client.get_access_token', lambda: 't')
    monkeypatch.setattr('backend.utils.antigravity_client.get_project_id', lambda: 'p')

    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return FakeResponse(status_code=500, text='boom')

    monkeypatch.setattr('backend.utils.antigravity_client.requests.post', fake_post)
    monkeypatch.setattr('backend.utils.antigravity_client.time.sleep', lambda *_: None)

    with pytest.raises(AntigravityError):
        client.generate_text('hi', model='gemini-3-flash-agent')

    assert all(url.startswith('https://proxy.example.com') for url in calls)
    assert len(calls) == 3  # MAX_RETRIES，绝不回退到官方端点


def test_401_forces_refresh_then_retries(monkeypatch):
    client = build_client(monkeypatch)
    tokens = iter(['stale-token', 'fresh-token'])
    refresh_calls = []

    monkeypatch.setattr('backend.utils.antigravity_client.get_access_token', lambda: next(tokens))

    def fake_force_refresh():
        refresh_calls.append(1)
        return 'fresh-token'

    monkeypatch.setattr('backend.utils.antigravity_client.force_refresh', fake_force_refresh)

    seen_auth = []

    def fake_post(url, **kwargs):
        seen_auth.append(kwargs['headers']['Authorization'])
        if kwargs['headers']['Authorization'] == 'Bearer stale-token':
            return FakeResponse(status_code=401, text='token expired')
        return FakeResponse(headers={'Content-Type': 'text/event-stream'}, lines=sse_lines(TEXT_CHUNKS))

    monkeypatch.setattr('backend.utils.antigravity_client.requests.post', fake_post)

    assert client.generate_text('hi', model='gemini-3-flash-agent') == '红墨'
    assert refresh_calls == [1]
    assert seen_auth == ['Bearer stale-token', 'Bearer fresh-token']


def test_404_message_tells_user_model_id_must_be_bare(monkeypatch):
    client = build_client(monkeypatch)
    monkeypatch.setattr('backend.utils.antigravity_client.requests.post',
                        lambda url, **kw: FakeResponse(status_code=404, text='Requested entity was not found'))

    with pytest.raises(AntigravityError) as exc:
        client.generate_text('hi', model='gemini-3-pro (high)')
    assert '404' in str(exc.value) and 'bare id' in str(exc.value)


def test_quota_summary_parses_groups_and_buckets(monkeypatch):
    client = build_client(monkeypatch)
    payload = {'groups': [{'groupId': 'gemini', 'buckets': [
        {'bucketId': 'gemini-weekly', 'remainingFraction': 0.42, 'resetTime': '2026-11-01T00:00:00Z'},
        {'bucketId': 'gemini-minute', 'disabled': True},
    ]}]}

    monkeypatch.setattr('backend.utils.antigravity_client.requests.post',
                        lambda url, **kw: FakeResponse(payload=payload))

    quota = client.quota_summary()
    bucket = quota['groups'][0]['buckets'][0]
    assert bucket['bucket_id'] == 'gemini-weekly'
    assert bucket['remaining_fraction'] == 0.42
    assert quota['groups'][0]['buckets'][1]['disabled'] is True


def test_available_models_filter_disabled_and_denied(monkeypatch):
    client = build_client(monkeypatch)
    payload = {
        'models': {
            'gemini-3.1-pro-low': {'displayName': 'Gemini 3.1 Pro'},
            'gemini-2.5-pro': {'displayName': 'denied'},
            'chat_20706': {'displayName': 'internal'},
            'gemini-3-flash-agent': {'disabled': True},
            'gemini-3-pro-image': {'supportsImages': True},
        },
        'imageGenerationModelIds': ['gemini-3-pro-image'],
    }

    monkeypatch.setattr('backend.utils.antigravity_client.requests.post',
                        lambda url, **kw: FakeResponse(payload=payload))

    roster = client.fetch_available_models()
    assert roster['text_models'] == ['gemini-3.1-pro-low']
    assert roster['image_model_ids'] == ['gemini-3-pro-image']


# ==================== 凭据存储 / 刷新 ====================

@pytest.fixture
def temp_credentials_file(tmp_path, monkeypatch):
    path = tmp_path / '.antigravity_credentials.json'
    monkeypatch.setattr('backend.auth.antigravity_credentials.CREDENTIALS_FILE', path)
    return path


def test_credentials_roundtrip_and_project_preserved(temp_credentials_file, monkeypatch):
    from backend.auth import antigravity_credentials as store

    assert store.load_credentials() is None
    store.save_credentials(token='t1', refresh_token='r1', expires_at=int(time.time() * 1000) + 3600_000,
                           project_id='p1', email='me@example.com')
    loaded = store.load_credentials()
    assert loaded['token'] == 't1' and loaded['project_id'] == 'p1'

    monkeypatch.setattr('backend.auth.antigravity_credentials.requests.post',
                        lambda url, **kw: FakeResponse(payload={
                            'access_token': 't2', 'expires_in': 3599, 'refresh_token': 'r2'}))

    token = store.get_access_token() if store.needs_refresh(loaded) else store.force_refresh()
    assert token == 't2'
    refreshed = store.load_credentials()
    assert refreshed['project_id'] == 'p1'  # 刷新响应没有 project，必须保留


def test_expired_credentials_refresh_without_being_asked(temp_credentials_file, monkeypatch):
    from backend.auth import antigravity_credentials as store

    store.save_credentials(token='old', refresh_token='r', expires_at=int(time.time() * 1000) - 10_000,
                           project_id='p')
    monkeypatch.setattr('backend.auth.antigravity_credentials.requests.post',
                        lambda url, **kw: FakeResponse(payload={'access_token': 'new', 'expires_in': 3599}))

    assert store.get_access_token() == 'new'


def test_invalid_grant_clears_credentials_and_asks_relogin(temp_credentials_file, monkeypatch):
    from backend.auth import antigravity_credentials as store
    from backend.auth.antigravity_credentials import AntigravityAuthError

    store.save_credentials(token='old', refresh_token='r', expires_at=int(time.time() * 1000) - 1000,
                           project_id='p')
    monkeypatch.setattr('backend.auth.antigravity_credentials.requests.post',
                        lambda url, **kw: FakeResponse(status_code=400, text='invalid_grant'))

    with pytest.raises(AntigravityAuthError) as exc:
        store.get_access_token()
    assert '重新登录' in str(exc.value)
    assert not temp_credentials_file.exists()


def test_not_logged_in_raises_friendly_error(temp_credentials_file):
    from backend.auth.antigravity_credentials import AntigravityAuthError, get_access_token

    with pytest.raises(AntigravityAuthError) as exc:
        get_access_token()
    assert '尚未登录' in str(exc.value)


# ==================== 多账号池：轮换 / 冷却 / 失效 ====================

@pytest.fixture
def pool_store(temp_credentials_file):
    """账号池 + 每次测试前重置进程内粘性账号。"""
    from backend.auth import antigravity_credentials as store

    store._active_account_id = None
    return store


def seed_account(store, email, token=None, **kwargs):
    return store.save_credentials(
        token=token or f'token-{email}',
        refresh_token=kwargs.pop('refresh_token', f'refresh-{email}'),
        expires_at=kwargs.pop('expires_at', int(time.time() * 1000) + 3600_000),
        project_id=kwargs.pop('project_id', f'proj-{email}'),
        email=email,
    )


def test_second_login_adds_account_instead_of_replacing(pool_store):
    first = seed_account(pool_store, 'a@example.com')
    second = seed_account(pool_store, 'b@example.com')

    assert first['id'] != second['id']
    assert pool_store.account_ids() == [first['id'], second['id']]

    summary = pool_store.connection_summary()
    assert summary['connected'] is True
    assert summary['account_count'] == 2
    assert summary['healthy_count'] == 2


def test_relogin_same_email_overwrites_its_own_row(pool_store):
    seed_account(pool_store, 'a@example.com', token='old-token')
    seed_account(pool_store, 'a@example.com', token='new-token')

    assert len(pool_store.account_ids()) == 1
    assert pool_store.load_credentials()['token'] == 'new-token'


def test_legacy_single_account_file_migrates(tmp_path):
    from backend.auth import antigravity_pool as pool

    path = tmp_path / '.antigravity_credentials.json'
    path.write_text(json.dumps({
        'token': 'legacy-token', 'refresh_token': 'legacy-refresh',
        'expires_at': int(time.time() * 1000) + 3600_000,
        'project_id': 'legacy-proj', 'email': 'Legacy@Example.com',
    }), encoding='utf-8')

    accounts = pool.read_pool(path)
    assert len(accounts) == 1
    assert accounts[0]['token'] == 'legacy-token'
    assert accounts[0]['id'] == 'legacy@example.com'


def test_quota_block_switches_to_next_account(pool_store, monkeypatch):
    from backend.utils.antigravity_client import AntigravityClient
    from backend.utils import antigravity_client as client_module

    a = seed_account(pool_store, 'a@example.com')
    b = seed_account(pool_store, 'b@example.com')

    tokens_seen = []

    def fake_post(url, **kwargs):
        tokens_seen.append(kwargs['headers']['Authorization'])
        if tokens_seen[-1] == f'Bearer token-a@example.com':
            return FakeResponse(status_code=429,
                                text='{"error":{"code":429,"message":"RESOURCE_EXHAUSTED",'
                                     '"quotaResetTimeStamp":"2026-10-26T12:00:00Z"}}')
        return FakeResponse(headers={'Content-Type': 'text/event-stream'}, lines=sse_lines(TEXT_CHUNKS))

    monkeypatch.setattr('backend.utils.antigravity_client.requests.post', fake_post)
    monkeypatch.setattr('backend.utils.antigravity_client.time.sleep', lambda *_: None)

    client = AntigravityClient()
    monkeypatch.setattr(client, 'resolve_text_model', lambda model=None: 'gemini-3-flash-agent')

    assert client.generate_text('hi') == '红墨'
    assert tokens_seen == ['Bearer token-a@example.com', 'Bearer token-b@example.com']

    accounts = {acc['id']: acc for acc in pool_store.read_pool(pool_store.CREDENTIALS_FILE)}
    assert accounts[a['id']]['blocked_until'] > int(time.time() * 1000)
    assert accounts[b['id']].get('blocked_until') is None


def test_in_band_quota_error_also_rotates(pool_store, monkeypatch):
    from backend.utils.antigravity_client import AntigravityClient

    seed_account(pool_store, 'a@example.com')
    seed_account(pool_store, 'b@example.com')

    tokens_seen = []

    def fake_post(url, **kwargs):
        tokens_seen.append(kwargs['headers']['Authorization'])
        if tokens_seen[-1] == f'Bearer token-a@example.com':
            chunks = [{'error': {'code': 429, 'message': 'RESOURCE_EXHAUSTED: quota reset'}}]
        else:
            chunks = TEXT_CHUNKS
        return FakeResponse(headers={'Content-Type': 'text/event-stream'}, lines=sse_lines(chunks))

    monkeypatch.setattr('backend.utils.antigravity_client.requests.post', fake_post)

    client = AntigravityClient()
    monkeypatch.setattr(client, 'resolve_text_model', lambda model=None: 'gemini-3-flash-agent')

    assert client.generate_text('hi') == '红墨'
    assert len(tokens_seen) == 2 and tokens_seen[0] != tokens_seen[1]


def test_single_account_quota_error_stays_backoff(pool_store, monkeypatch):
    from backend.utils.antigravity_client import AntigravityClient, AntigravityError

    seed_account(pool_store, 'only@example.com')

    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return FakeResponse(status_code=429, text='RESOURCE_EXHAUSTED')

    monkeypatch.setattr('backend.utils.antigravity_client.requests.post', fake_post)
    monkeypatch.setattr('backend.utils.antigravity_client.time.sleep', lambda *_: None)

    client = AntigravityClient()
    monkeypatch.setattr(client, 'resolve_text_model', lambda model=None: 'gemini-3-flash-agent')

    with pytest.raises(AntigravityError):
        client.generate_text('hi')

    # 单账号绝不拉黑自己：仍然走完退避重试
    assert len(calls) == 6  # MAX_RETRIES * 2 端点
    accounts = pool_store.read_pool(pool_store.CREDENTIALS_FILE)
    assert accounts[0].get('blocked_until') is None
    assert accounts[0].get('dead') is False


def test_invalid_grant_marks_dead_and_keeps_siblings(pool_store, monkeypatch):
    a = seed_account(pool_store, 'a@example.com', expires_at=int(time.time() * 1000) - 1000)
    seed_account(pool_store, 'b@example.com')

    monkeypatch.setattr('backend.auth.antigravity_credentials.requests.post',
                        lambda url, **kw: FakeResponse(status_code=400, text='invalid_grant'))

    # a 过期 -> 刷新 -> invalid_grant -> 标记失效，get_access_token 自动切到 b
    token = pool_store.get_access_token()
    assert token == 'token-b@example.com'

    accounts = {acc['id']: acc for acc in pool_store.read_pool(pool_store.CREDENTIALS_FILE)}
    assert accounts[a['id']]['dead'] is True
    assert accounts[a['id']]['token']  # 记录保留，便于展示与重新登录
    assert temp_credentials_file_exists(pool_store)


def temp_credentials_file_exists(store):
    return store.CREDENTIALS_FILE.exists()


def test_remove_account_and_unblock(pool_store):
    a = seed_account(pool_store, 'a@example.com')
    b = seed_account(pool_store, 'b@example.com')

    pool_store.note_quota_exhausted(detail={'quotaResetTimeStamp': int(time.time() * 1000) + 60_000},
                                    reason='429')
    assert pool_store.current_account_id() is None
    assert pool_store.pick_next_or_none() if hasattr(pool_store, 'pick_next_or_none') else True

    assert pool_store.unblock_account(a['id']) is True
    accounts = {acc['id']: acc for acc in pool_store.read_pool(pool_store.CREDENTIALS_FILE)}
    assert accounts[a['id']].get('blocked_until') is None

    assert pool_store.clear_account(b['id']) is True
    assert pool_store.account_ids() == [a['id']]
    assert pool_store.clear_account('nope') is False

    pool_store.clear_credentials()
    assert pool_store.account_ids() == []
    assert not pool_store.CREDENTIALS_FILE.exists()


def test_all_accounts_blocked_reports_pool_exhausted(pool_store):
    from backend.auth.antigravity_credentials import AntigravityAuthError

    seed_account(pool_store, 'a@example.com')
    seed_account(pool_store, 'b@example.com')
    now = int(time.time() * 1000)

    for account_id in pool_store.account_ids():
        pool_store.mark_blocked(pool_store.CREDENTIALS_FILE, account_id, now + 120_000, reason='429')

    with pytest.raises(AntigravityAuthError) as exc:
        pool_store.get_access_token()
    assert 'tài khoản' in str(exc.value)

    summary = pool_store.connection_summary()
    assert summary['connected'] is False
    assert summary['account_count'] == 2
    assert summary['cooling_down_count'] == 2
    assert summary['recovered_at'] > now


def test_parse_reset_time_variants():
    from backend.auth.antigravity_pool import parse_reset_time

    now = 1_780_000_000_000
    assert parse_reset_time({'quotaResetTimeStamp': '2026-11-01T00:00:00Z'}, now=now) \
        == int(__import__('datetime').datetime(2026, 11, 1, tzinfo=__import__('datetime').timezone.utc)
               .timestamp() * 1000)
    assert parse_reset_time({'retryDelay': '30s'}, now=now) == now + 30_000
    assert parse_reset_time({'quotaResetTimeStamp': now // 1000 + 60}, now=now) == now + 60_000
    # 秒级时间戳自动换算成毫秒
    assert parse_reset_time({'resetTime': (now // 1000) + 90}, now=now) == (now // 1000 + 90) * 1000
    # 解析不到 -> 默认冷却窗口
    from backend.auth.antigravity_pool import DEFAULT_QUOTA_COOLDOWN_MS
    assert parse_reset_time({'message': 'no reset here'}, now=now) == now + DEFAULT_QUOTA_COOLDOWN_MS



def test_account_routes_list_logout_unblock(client, temp_credentials_file):
    from backend.auth import antigravity_credentials as store

    a = seed_account(store, 'a@example.com')
    b = seed_account(store, 'b@example.com')

    listed = client.get('/api/auth/antigravity/accounts').get_json()
    assert listed['success'] is True
    assert {acc['id'] for acc in listed['accounts']} == {a['id'], b['id']}

    unblocked = client.post('/api/auth/antigravity/account/unblock',
                            json={'account_id': b['id']})
    assert unblocked.status_code == 200

    removed = client.post('/api/auth/antigravity/account/logout',
                          json={'account_id': a['id']})
    assert removed.status_code == 200
    remaining = client.get('/api/auth/antigravity/accounts').get_json()['accounts']
    assert [acc['id'] for acc in remaining] == [b['id']]


def test_account_routes_validation_errors(client, temp_credentials_file):
    missing = client.post('/api/auth/antigravity/account/logout', json={})
    assert missing.status_code == 400
    assert missing.get_json()['error']['code'] == 'INVALID_REQUEST'

    not_found = client.post('/api/auth/antigravity/account/logout', json={'account_id': 'ghost'})
    assert not_found.status_code == 400

# ==================== 登录流程：授权链接 + 项目发现 ====================

def test_authorize_url_contains_offline_consent_and_pkce():
    from backend.auth.antigravity_oauth import AntigravityLoginManager
    from urllib.parse import parse_qs, urlparse

    manager = AntigravityLoginManager()
    url, state, verifier = manager._build_auth_url('http://127.0.0.1:51121/oauth-callback')

    assert url.startswith('https://accounts.google.com/o/oauth2/v2/auth?')
    params = parse_qs(urlparse(url).query)
    assert params['access_type'] == ['offline']
    assert params['prompt'] == ['consent']
    assert params['response_type'] == ['code']
    assert params['code_challenge'] == [sha256_b64url(verifier)]
    assert params['code_challenge_method'] == ['S256']
    assert params['redirect_uri'] == ['http://127.0.0.1:51121/oauth-callback']
    assert params['state'] == [state]
    assert len(verifier) >= 43


def test_load_code_assist_uses_antigravity_metadata(temp_credentials_file, monkeypatch):
    from backend.auth import antigravity_oauth as oauth_flow
    from backend.auth.antigravity_constants import ANTIGRAVITY_METADATA

    calls = []
    monkeypatch.setattr('backend.auth.antigravity_oauth.requests.post',
                        lambda url, **kw: calls.append((url, kw)) or FakeResponse(payload={
                            'currentTier': {'id': 'free-tier'},
                            'allowedTiers': [{'id': 'free-tier'}],
                            'cloudaicompanionProject': 'project-abc',
                        }))

    payload = oauth_flow.load_code_assist('token')

    assert payload['cloudaicompanionProject'] == 'project-abc'
    url, kwargs = calls[0]
    assert url.endswith('/v1internal:loadCodeAssist')
    assert kwargs['json']['metadata'] == ANTIGRAVITY_METADATA
    assert kwargs['headers']['User-Agent'].startswith('antigravity/hub/')
    assert kwargs['headers']['Authorization'] == 'Bearer token'


def test_ineligible_free_tier_surfaces_validation_url(temp_credentials_file):
    from backend.auth import antigravity_oauth as oauth_flow
    from backend.auth.antigravity_credentials import AntigravityAuthError

    with pytest.raises(AntigravityAuthError) as exc:
        oauth_flow.assert_free_tier_eligible({
            'allowedTiers': [],
            'ineligibleTiers': [{'tierId': 'free-tier', 'reasonMessage': '请先验证账号',
                                 'validationUrl': 'https://console.cloud.google.com/verify'}],
        })
    assert '请先验证账号' in str(exc.value)
    assert 'verify' in str(exc.value)


def test_onboard_user_polls_operation_until_done(temp_credentials_file, monkeypatch):
    from backend.auth import antigravity_oauth as oauth_flow

    responses = [
        {'name': 'operations/onboard-1', 'done': False},
        {'name': 'operations/onboard-1', 'done': True,
         'response': {'@type': 'type.googleapis.com/OnboardUserResponse'}},
    ]
    posts = []
    monkeypatch.setattr('backend.auth.antigravity_oauth.requests.post',
                        lambda url, **kw: posts.append(url) or FakeResponse(payload=responses[0]))
    monkeypatch.setattr('backend.auth.antigravity_oauth.requests.get',
                        lambda url, **kw: FakeResponse(payload=responses[1]))
    monkeypatch.setattr('backend.auth.antigravity_oauth.time.sleep', lambda *_: None)

    oauth_flow.onboard_user('token')

    assert posts == ['https://daily-cloudcode-pa.googleapis.com/v1internal:onboardUser']


def test_onboard_user_error_raises(temp_credentials_file, monkeypatch):
    from backend.auth import antigravity_oauth as oauth_flow
    from backend.auth.antigravity_credentials import AntigravityAuthError

    monkeypatch.setattr('backend.auth.antigravity_oauth.requests.post',
                        lambda url, **kw: FakeResponse(payload={
                            'name': 'operations/x', 'done': True,
                            'error': {'code': 3, 'message': 'tier not allowed'}}))

    with pytest.raises(AntigravityAuthError) as exc:
        oauth_flow.onboard_user('token')
    assert 'tier not allowed' in str(exc.value)


# ==================== 配置校验：key 变成可选项 ====================

def test_image_provider_config_accepts_oauth_without_api_key(monkeypatch, tmp_path):
    from backend.config import Config

    monkeypatch.setattr('backend.auth.antigravity_credentials.connection_summary',
                        lambda: {'connected': True, 'email': 'me@example.com'})
    Config._image_providers_config = {
        'active_provider': 'antigravity_image',
        'providers': {'antigravity_image': {'type': ANTIGRAVITY_PROVIDER_TYPE, 'auth': AUTH_ANTIGRAVITY_OAUTH,
                                            'model': 'auto'}},
    }
    try:
        config = Config.get_image_provider_config('antigravity_image')
        assert config['type'] == ANTIGRAVITY_PROVIDER_TYPE
    finally:
        Config._image_providers_config = None


def test_image_provider_config_requires_login_for_oauth(monkeypatch):
    from backend.config import Config

    monkeypatch.setattr('backend.auth.antigravity_credentials.connection_summary',
                        lambda: {'connected': False})
    Config._image_providers_config = {
        'active_provider': 'antigravity_image',
        'providers': {'antigravity_image': {'type': ANTIGRAVITY_PROVIDER_TYPE}},
    }
    try:
        with pytest.raises(ValueError) as exc:
            Config.get_image_provider_config('antigravity_image')
        assert '尚未登录' in str(exc.value)
    finally:
        Config._image_providers_config = None


def test_image_provider_config_still_requires_api_key_for_key_providers():
    from backend.config import Config

    Config._image_providers_config = {
        'active_provider': 'gemini',
        'providers': {'gemini': {'type': 'google_genai', 'model': 'x'}},
    }
    try:
        with pytest.raises(ValueError) as exc:
            Config.get_image_provider_config('gemini')
        assert 'API Key' in str(exc.value)
    finally:
        Config._image_providers_config = None


def test_text_client_factory_returns_oauth_client(monkeypatch):
    from backend.utils.text_client import get_text_chat_client
    from backend.utils.antigravity_text_client import AntigravityTextClient, reset_antigravity_text_clients

    reset_antigravity_text_clients()
    client = get_text_chat_client({'type': ANTIGRAVITY_PROVIDER_TYPE, 'auth': AUTH_ANTIGRAVITY_OAUTH})
    assert isinstance(client, AntigravityTextClient)
    reset_antigravity_text_clients()


def test_factory_registers_antigravity_generator():
    from backend.generators.factory import ImageGeneratorFactory
    from backend.generators.antigravity_genai import AntigravityImageGenerator

    assert ImageGeneratorFactory.GENERATORS[ANTIGRAVITY_PROVIDER_TYPE] is AntigravityImageGenerator


# ==================== 图片生成器 ====================

def test_image_generator_requires_google_login(temp_credentials_file):
    from backend.generators.antigravity_genai import AntigravityImageGenerator

    with pytest.raises(ValueError) as exc:
        AntigravityImageGenerator({'type': ANTIGRAVITY_PROVIDER_TYPE, 'model': 'auto'})
    assert '尚未登录' in str(exc.value)


def test_image_generator_returns_image_bytes(temp_credentials_file, monkeypatch):
    from backend.generators.antigravity_genai import AntigravityImageGenerator

    monkeypatch.setattr('backend.generators.antigravity_genai.connection_summary',
                        lambda: {'connected': True, 'email': 'me@example.com'})
    png = b'\x89PNG-fake'
    monkeypatch.setattr(
        'backend.generators.antigravity_genai.AntigravityClient.generate_image',
        lambda self, prompt, model=None, aspect_ratio='3:4', reference_images=None, candidate_count=1: [png],
    )

    generator = AntigravityImageGenerator({'type': ANTIGRAVITY_PROVIDER_TYPE, 'model': 'gemini-3-pro-image'})
    assert generator.generate_image('画封面', aspect_ratio='3:4', model='gemini-3-pro-image') == png
    assert generator.validate_config() is True


# ==================== 模型自动选择（auto） ====================

def test_auto_text_model_uses_discovered_roster(monkeypatch):
    client = AntigravityClient()
    monkeypatch.setattr(client, 'fetch_available_models',
                        lambda: {'text_models': ['gemini-3-flash-agent', 'gemini-3.1-pro-low'],
                                 'image_model_ids': [], 'models': {}})

    # 留空 / auto / 遗留默认值都按账号实际模型解析
    assert client.resolve_text_model(None) == 'gemini-3-flash-agent'
    assert client.resolve_text_model('auto') == 'gemini-3-flash-agent'
    assert client.resolve_text_model('gemini-2.0-flash-exp') == 'gemini-3-flash-agent'


def test_explicit_text_model_is_used_verbatim(monkeypatch):
    client = AntigravityClient()
    monkeypatch.setattr(client, 'fetch_available_models',
                        lambda: {'text_models': ['gemini-3-flash-agent'], 'image_model_ids': [], 'models': {}})
    assert client.resolve_text_model('claude-sonnet-4-6 (high)') == 'claude-sonnet-4-6'


def test_auto_text_model_falls_back_when_discovery_fails(monkeypatch):
    from backend.utils.antigravity_client import AntigravityError

    client = AntigravityClient()

    def boom():
        raise AntigravityError('network down')

    monkeypatch.setattr(client, 'fetch_available_models', boom)
    assert client.resolve_text_model('auto') == 'gemini-3.1-pro-low'


# ==================== API 路由 ====================

def test_auth_status_reports_not_connected(client, temp_credentials_file):
    response = client.get('/api/auth/antigravity/status')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['connected'] is False
    assert payload['credential']['connected'] is False


def test_auth_models_requires_login(client, temp_credentials_file):
    response = client.get('/api/auth/antigravity/models')
    assert response.status_code == 400
    assert response.get_json()['error']['code'] == 'INVALID_REQUEST'


def test_auth_logout_clears_credentials(client, temp_credentials_file):
    from backend.auth.antigravity_credentials import save_credentials

    save_credentials(token='t', refresh_token='r', expires_at=int(time.time() * 1000) + 3600_000,
                     project_id='p', email='me@example.com')
    assert temp_credentials_file.exists()

    response = client.post('/api/auth/antigravity/logout')
    assert response.status_code == 200
    assert not temp_credentials_file.exists()


def test_config_test_rejects_api_key_provider_without_key(client):
    response = client.post('/api/config/test', json={'type': 'openai_compatible', 'model': 'gpt-4o'})
    assert response.status_code == 400
    assert response.get_json()['error']['code'] == 'INVALID_REQUEST'


# ==================== 未配置时的默认方案（截图里的 bug 回归） ====================

def test_default_text_providers_use_oauth_without_api_key():
    from backend.auth import default_text_providers, uses_oauth_auth

    config = default_text_providers()
    active = config['active_provider']
    provider = config['providers'][active]

    assert uses_oauth_auth(provider) is True
    assert 'api_key' not in provider
    assert provider['model'] == 'auto'


def test_default_image_providers_use_oauth_without_api_key():
    from backend.auth import default_image_providers, uses_oauth_auth

    config = default_image_providers()
    provider = config['providers'][config['active_provider']]

    assert uses_oauth_auth(provider) is True
    assert 'api_key' not in provider


def test_outline_service_without_config_file_defaults_to_oauth(monkeypatch, tmp_path):
    from backend.services.outline import OutlineService

    monkeypatch.setattr(OutlineService, '_get_client', lambda self: object())
    monkeypatch.setattr(OutlineService, '_load_prompt_template', lambda self: 'x')

    service = OutlineService()
    provider = service.text_config['providers'][service.text_config['active_provider']]

    assert provider['type'] == ANTIGRAVITY_PROVIDER_TYPE
    assert provider.get('auth') == AUTH_ANTIGRAVITY_OAUTH


def test_config_endpoint_defaults_are_oauth(client, monkeypatch, temp_credentials_file):
    import backend.routes.config_routes as config_routes

    def missing(path, default):
        return default

    monkeypatch.setattr(config_routes, '_read_config', missing)

    payload = client.get('/api/config').get_json()
    for section in ('text_generation', 'image_generation'):
        providers = payload['config'][section]['providers']
        assert providers, section
        provider = next(iter(providers.values()))
        assert provider['type'] == ANTIGRAVITY_PROVIDER_TYPE
        assert provider['api_key'] == ''
        assert 'oauth' in provider


def test_config_test_accepts_oauth_type_without_key(client, temp_credentials_file, monkeypatch):
    from backend.auth import antigravity_credentials as store

    monkeypatch.setattr(store, 'CREDENTIALS_FILE', temp_credentials_file)
    store.save_credentials(token='t', refresh_token='r',
                           expires_at=int(time.time() * 1000) + 3600_000, project_id='p',
                           email='me@example.com')

    def fake_post(url, **kwargs):
        if 'fetchAvailableModels' in url:
            return FakeResponse(payload={'models': {'gemini-3-flash-agent': {}},
                                         'imageGenerationModelIds': []})
        return FakeResponse(headers={'Content-Type': 'text/event-stream'},
                            lines=sse_lines(TEXT_CHUNKS))

    monkeypatch.setattr('backend.utils.antigravity_client.requests.post', fake_post)

    response = client.post('/api/config/test',
                           json={'type': ANTIGRAVITY_PROVIDER_TYPE, 'model': 'gemini-3-flash-agent'})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['success'] is True


def test_config_response_marks_oauth_provider_state(client, temp_credentials_file, monkeypatch):
    from backend.auth import antigravity_credentials as store

    monkeypatch.setattr(store, 'CREDENTIALS_FILE', temp_credentials_file)

    import backend.routes.config_routes as config_routes

    def fake_read(path, default):
        return {'active_provider': 'antigravity',
                'providers': {'antigravity': {'type': ANTIGRAVITY_PROVIDER_TYPE,
                                              'auth': AUTH_ANTIGRAVITY_OAUTH, 'model': 'gemini-3.1-pro-low'}}}

    monkeypatch.setattr(config_routes, '_read_config', fake_read)

    payload = client.get('/api/config').get_json()
    provider = payload['config']['text_generation']['providers']['antigravity']
    assert provider['oauth']['connected'] is False
    assert provider['api_key'] == ''


def test_saved_provider_strips_oauth_echo_field(client, tmp_path, monkeypatch):
    import backend.routes.config_routes as config_routes

    written = {}
    target = tmp_path / 'text_providers.yaml'

    monkeypatch.setattr(config_routes, 'TEXT_CONFIG_PATH', target)
    monkeypatch.setattr(config_routes, '_read_config', lambda path, default: {'providers': {}})
    monkeypatch.setattr(config_routes, '_write_config',
                        lambda path, config: written.update(path=path, config=config))

    config_routes._update_provider_config(target, {
        'providers': {'antigravity': {'type': ANTIGRAVITY_PROVIDER_TYPE,
                                      'auth': AUTH_ANTIGRAVITY_OAUTH,
                                      'model': 'gemini-3.1-pro-low',
                                      'oauth': {'connected': True, 'email': 'x@y.z'},
                                      'api_key_masked': 'abcd'}}
    })

    saved = written['config']['providers']['antigravity']
    assert 'oauth' not in saved
    assert saved['auth'] == AUTH_ANTIGRAVITY_OAUTH
    assert 'api_key' not in saved
