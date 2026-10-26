"""
Antigravity（Cloud Code Assist）生成客户端

与 oh-my-pi 的 google-gemini-cli/google-antigravity provider 保持同一套线上验证过的协议：

- 请求：POST {endpoint}/v1internal:streamGenerateContent?alt=sse
  信封：{project, model, request{contents, systemInstruction, generationConfig, labels, sessionId}, requestId, userAgent}
- 模型 id 必须是裸 id（带 "(high)" 这类注释会 404）
- 必须携带 antigravity/hub UA，否则配额读取与模型门控会 403/降级
- daily 端点失败自动回退 sandbox 端点
- 401 触发一次强制刷新 token 后重试；429/5xx 走退避重试

多账号（Google 账号登录池）：
- 上游 429 / 内联配额错误 -> 当前账号进入冷却（尊重 quotaResetTimeStamp），自动切到下一个健康账号
- 凭据被撤销（invalid_grant）/ 刷新失败 -> 当前账号标记失效或短冷却，同样切换
- 池里只有一个账号时退化为原来的退避重试，不做任何拉黑
"""
import base64
import hashlib
import json
import logging
import random
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Union

import requests

from ..auth.antigravity_constants import (
    DEFAULT_IMAGE_MODEL,
    DEFAULT_MODEL,
    ENDPOINT_FALLBACKS,
    GENERATE_CONTENT_PATH,
    IMAGE_MODELS,
    RETRIEVE_QUOTA_PATH,
    STREAM_GENERATE_CONTENT_PATH,
    FETCH_AVAILABLE_MODELS_PATH,
    antigravity_user_agent,
    model_wire_profile,
    strip_effort_annotation,
)
from ..auth.antigravity_credentials import (
    AntigravityAuthError,
    account_ids,
    current_account_id,
    force_refresh,
    get_access_token,
    get_project_id,
    note_credentials_dead,
    note_quota_exhausted,
    note_refresh_failed,
    rotation_available,
)
from ..auth.antigravity_pool import looks_like_quota_error, parse_reset_time

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0
# 一次请求最多切换多少个账号（防止病态轮询）
MAX_ACCOUNT_SWITCHES = 4
IMAGE_SYSTEM_INSTRUCTION = (
    "You are an AI image generator. Generate images based on user descriptions. "
    "Focus on creating high-quality, visually appealing images that match the user's request."
)
SAFETY_SETTINGS = [
    {"category": category, "threshold": "BLOCK_ONLY_HIGH"}
    for category in (
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
        "HARM_CATEGORY_CIVIC_INTEGRITY",
    )
]


class AntigravityError(Exception):
    """Antigravity 调用失败（已带用户可读信息）。"""


class AntigravityQuotaError(AntigravityError):
    """配额/限流（HTTP 429 或 SSE 内联错误）；多账号时可触发自动切换。"""

    def __init__(self, message: str, detail: Any = None):
        super().__init__(message)
        self.detail = detail


def derive_session_id(seed: str) -> str:
    """把会话首条内容哈希成有符号十进制字符串（对齐官方客户端的 sessionId 形态）。"""
    digest = hashlib.sha256((seed or '').encode('utf-8')).digest()
    return str(int.from_bytes(digest[:8], 'big', signed=True))


class AntigravityClient:
    """Cloud Code Assist 客户端：文本 + 图片生成、模型发现、配额查询。"""

    def __init__(self, base_url: str = None, timeout: int = 300):
        self.timeout = timeout
        self._endpoints = self._build_endpoints(base_url)
        self._last_good_endpoint: Optional[str] = None
        self._access_token: Optional[str] = None
        self._project_id: Optional[str] = None
        self._agent_id = str(uuid.uuid4())
        self._trajectory_id = str(uuid.uuid4())
        self._step_index = 1
        self._resolved_text_model: Optional[str] = None
        self._resolved_image_model: Optional[str] = None

    @staticmethod
    def _build_endpoints(base_url: str = None) -> List[str]:
        if base_url:
            cleaned = base_url.rstrip('/')
            if cleaned not in ENDPOINT_FALLBACKS:
                return [cleaned]
        return list(ENDPOINT_FALLBACKS)

    # ---------- 凭据 ----------

    def credentials(self) -> (str, str):
        self._access_token = get_access_token()
        self._project_id = get_project_id()
        return self._access_token, self._project_id

    def reset_session(self):
        """一个新的生成任务对应一个新的 agent/trajectory 身份。"""
        self._agent_id = str(uuid.uuid4())
        self._trajectory_id = str(uuid.uuid4())
        self._step_index = 1

    # ---------- 请求（账号轮换外壳） ----------

    def _request(self, path: str, body: dict, consume: Callable, stream: bool = False,
                 timeout: int = None):
        """
        执行一次生成：失败时按账号池自动切换。

        consume(response) 负责解析响应；解析里抛出的 AntigravityQuotaError
        同样会触发换号重试（Antigravity 的配额错误经常以 HTTP 200 + SSE error 出现）。
        """
        switch_budget = min(MAX_ACCOUNT_SWITCHES, max(0, len(account_ids()) - 1))
        tried: List[str] = []
        cooldown_hint: Optional[int] = None

        while True:
            account_id = current_account_id()
            try:
                response = self._post(path, body, stream=stream, timeout=timeout)
                return consume(response)
            except AntigravityQuotaError as exc:
                cooldown = note_quota_exhausted(detail=exc.detail, reason=str(exc))
                if cooldown is None or switch_budget <= 0:
                    raise
                cooldown_hint = cooldown
                tried.append(account_id)
                switch_budget -= 1
                logger.warning(
                    f"Antigravity 账号 {account_id} 配额耗尽，切换到下一个账号"
                    f"（冷却 {cooldown / 1000:.0f}s，剩余切换次数 {switch_budget}）"
                )
                self.reset_session()
                continue
            except AntigravityAuthError as exc:
                if switch_budget <= 0 or not rotation_available(exclude=tried + [account_id]):
                    raise
                note_refresh_failed(reason=str(exc))
                tried.append(account_id)
                switch_budget -= 1
                logger.warning(f"Antigravity 账号 {account_id} 凭据不可用，切换到下一个账号: {exc}")
                self.reset_session()
                continue

    # ---------- 请求（单账号） ----------

    def _post(self, path: str, body: dict, stream: bool = False, timeout: int = None):
        """
        带端点回退 + 401 刷新 + 退避重试的 POST。

        多账号池存在时，429 直接抛 AntigravityQuotaError 交给上层换号，
        单账号则保持原来的退避重试。

        Returns:
            requests.Response
        """
        last_error: Optional[str] = None
        attempted_refresh = False
        can_rotate = rotation_available(exclude=[current_account_id()])

        for attempt in range(MAX_RETRIES):
            access_token, project_id = self.credentials()
            body_with_project = dict(body)
            body_with_project.setdefault('project', project_id)

            for endpoint in self._ordered_endpoints():
                url = f"{endpoint}{path}"
                try:
                    response = requests.post(
                        url,
                        json=body_with_project,
                        headers=self._headers(access_token),
                        timeout=timeout or self.timeout,
                        stream=stream,
                    )
                except requests.RequestException as exc:
                    last_error = f"{url} 请求失败: {exc}"
                    logger.warning(last_error)
                    continue

                if response.status_code == 200:
                    self._last_good_endpoint = endpoint
                    return response

                detail = (response.text or '')[:400]
                last_error = f"HTTP {response.status_code}: {detail}"

                if response.status_code == 401 and not attempted_refresh:
                    attempted_refresh = True
                    logger.info("Antigravity 返回 401，刷新登录凭据后重试")
                    try:
                        force_refresh()
                    except AntigravityAuthError as exc:
                        raise AntigravityAuthError(str(exc))
                    break  # 换 token 后重新走一轮端点

                if response.status_code == 401:
                    note_credentials_dead(reason='401 after refresh')
                    raise AntigravityAuthError(
                        "Thông tin xác thực đăng nhập Google vẫn bị từ chối (401), vui lòng 重新登录 trong trang Cài đặt hệ thống."
                    )

                if response.status_code == 403:
                    raise AntigravityError(
                        "❌ Antigravity 拒绝了请求（403）\n\n"
                        "【Nguyên nhân có thể】\n"
                        "1. Tài khoản Google này chưa kích hoạt / không đủ điều kiện dùng gói miễn phí Antigravity\n"
                        "2. Khu vực của tài khoản hoặc trạng thái xác minh tài khoản bị giới hạn\n\n"
                        "【Giải pháp】\n"
                        "1. Đăng nhập lại trong Cài đặt hệ thống, xác nhận tài khoản đã hoàn tất xác minh cần thiết\n"
                        f"【上游响应】{detail}"
                    )
                if response.status_code == 404:
                    raise AntigravityError(
                        "❌ 模型不存在（404）\n\n"
                        f"【Nguyên nhân】Model id phải là bare id, và phải là model khả dụng cho tài khoản này.\n"
                        f"Model hiện tại: {body.get('model')}\n"
                        "【Giải pháp】\n"
                        "1. Bỏ phần hậu tố trong ngoặc sau tên model (ví dụ: (high)/(low))\n"
                        "2. Chuyển sang model khả dụng cho tài khoản trong Cài đặt; xem model khả dụng tại 'Lấy danh sách model khả dụng' trên trang Cài đặt\n"
                        f"【上游响应】{detail}"
                    )
                if response.status_code == 429:
                    if can_rotate:
                        raise AntigravityQuotaError(
                            f"❌ Antigravity 配额耗尽（429）\n【上游响应】{detail}",
                            detail=self._error_payload(response),
                        )
                    logger.warning(f"Antigravity 限流 (429)，准备重试: {detail[:200]}")
                    continue
                if response.status_code >= 500:
                    logger.warning(f"Antigravity 临时失败 ({response.status_code})，准备重试: {detail[:200]}")
                    continue

                raise AntigravityError(
                    f"❌ Antigravity yêu cầu thất bại (HTTP {response.status_code})\n"
                    f"【上游响应】{detail}"
                )
            else:
                # 所有端点都失败：429/5xx 退避后重试
                if attempt < MAX_RETRIES - 1:
                    wait_time = (RETRY_BASE_DELAY ** attempt) + random.uniform(0, 1)
                    logger.info(f"[重试] Antigravity 限流/临时错误，{wait_time:.1f}s 后重试 "
                                f"(尝试 {attempt + 2}/{MAX_RETRIES})")
                    time.sleep(wait_time)
                    continue

        raise AntigravityError(
            f"⚠️ Antigravity yêu cầu 重试 {MAX_RETRIES} 次仍失败。\n"
            f"【Lỗi lần cuối】{last_error}\n\n"
            "【Nguyên nhân có thể】\n"
            "1. Hạn mức gói miễn phí 配额 đã dùng hết (Antigravity có cửa sổ hạn mức phút/tuần)\n"
            "2. Mạng 网络 không thể truy cập Google (cần proxy)\n"
            "3. Dịch vụ tạm thời không khả dụng, vui lòng thử lại sau"
        )

    @staticmethod
    def _error_payload(response) -> Optional[dict]:
        try:
            return response.json()
        except (ValueError, TypeError):
            return None

    def _headers(self, access_token: str) -> Dict[str, str]:
        return {
            'Authorization': f"Bearer {access_token}",
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream',
            'User-Agent': antigravity_user_agent(),
        }

    def _ordered_endpoints(self) -> List[str]:
        endpoints = list(self._endpoints)
        if self._last_good_endpoint in endpoints:
            endpoints.remove(self._last_good_endpoint)
            endpoints.insert(0, self._last_good_endpoint)
        return endpoints

    # ---------- 信封 ----------

    def _build_envelope(
        self,
        model: str,
        contents: List[dict],
        system_instruction: str = None,
        generation_config: Dict[str, Any] = None,
        session_seed: str = '',
    ) -> dict:
        wire_model = strip_effort_annotation(model)
        profile = model_wire_profile(wire_model)

        request: Dict[str, Any] = {'contents': contents}

        if system_instruction:
            # 真实客户端把 systemInstruction 标成 role=user
            request['systemInstruction'] = {'role': 'user', 'parts': [{'text': system_instruction}]}

        config = dict(generation_config or {})
        if profile.get('max_output_tokens') and 'maxOutputTokens' in config:
            config['maxOutputTokens'] = min(
                int(config['maxOutputTokens']),
                int(profile['max_output_tokens']),
            )
        if config:
            request['generationConfig'] = config

        self._step_index += 1
        step = self._step_index
        labels = {
            'last_step_index': str(step - 1),
            'trajectory_id': self._trajectory_id,
            'used_claude': str('claude' in wire_model.lower()),
        }
        labels['used_claude_conservative'] = labels['used_claude']
        if profile.get('model_enum'):
            labels['model_enum'] = profile['model_enum']
        request['labels'] = labels
        request['sessionId'] = derive_session_id(session_seed or wire_model)

        return {
            'model': wire_model,
            'requestId': f"agent/{self._agent_id}/{int(time.time() * 1000)}/{self._trajectory_id}/{step}",
            'request': request,
            'userAgent': 'antigravity',
        }

    # ---------- 文本 ----------
    # 老配置里的默认模型在 Antigravity 上不存在，遇到时按 auto 处理
    LEGACY_TEXT_DEFAULTS = {'gemini-2.0-flash-exp', 'gemini-3-pro-preview'}

    def resolve_text_model(self, model: str = None) -> str:
        """配置留空/auto/遗留默认值时，用账号实际提供的第一个文本模型（结果缓存）。"""
        requested = (model or '').strip()
        if requested and requested.lower() not in ('auto', 'antigravity', 'default') \
                and requested not in self.LEGACY_TEXT_DEFAULTS:
            return strip_effort_annotation(requested)

        if self._resolved_text_model:
            return self._resolved_text_model

        try:
            available = self.fetch_available_models().get('text_models') or []
        except (AntigravityError, AntigravityAuthError) as exc:
            logger.warning(f"Antigravity 模型发现失败，回退默认模型: {exc}")
            return DEFAULT_MODEL

        if not available:
            return DEFAULT_MODEL
        self._resolved_text_model = available[0]
        logger.info(f"Antigravity 自动选择文本模型: {self._resolved_text_model}")
        return self._resolved_text_model

    def generate_text(
        self,
        prompt: str,
        model: str = None,
        temperature: float = 1.0,
        max_output_tokens: int = 8000,
        images: List[Union[bytes, str]] = None,
        system_prompt: str = None,
    ) -> str:
        contents = [{'role': 'user', 'parts': self._build_parts(prompt, images)}]
        envelope = self._build_envelope(
            model=self.resolve_text_model(model),
            contents=contents,
            system_instruction=system_prompt,
            generation_config={'maxOutputTokens': int(max_output_tokens), 'temperature': float(temperature)},
            session_seed=prompt,
        )

        text = self._request(
            STREAM_GENERATE_CONTENT_PATH,
            envelope,
            consume=self._collect_stream_text,
            stream=True,
        )

        if not text.strip():
            raise AntigravityError(
                "⚠️ Antigravity đã trả về 空响应。\n"
                "【Giải pháp】\n"
                "1. Thử lại một lần nữa (model này thỉnh thoảng trả về stream rỗng)\n"
                "2. Đổi sang model khác, hoặc rút ngắn prompt"
            )
        return text

    @staticmethod
    def _build_parts(text: str, images: List[Union[bytes, str]] = None) -> List[dict]:
        parts: List[dict] = []
        for image in images or []:
            if isinstance(image, (bytes, bytearray)):
                from ..utils.image_compressor import compress_image

                compressed = compress_image(image, max_size_kb=200)
                parts.append({
                    'inlineData': {
                        'mimeType': 'image/png',
                        'data': base64.b64encode(compressed).decode('utf-8'),
                    }
                })
            elif isinstance(image, str) and image.startswith('data:'):
                header, _, payload = image.partition(',')
                mime = header[5:].split(';')[0] or 'image/png'
                parts.append({'inlineData': {'mimeType': mime, 'data': payload}})
            elif isinstance(image, str):
                parts.append({'fileData': {'mimeType': 'image/png', 'fileUri': image}})
        parts.append({'text': text})
        return parts

    @staticmethod
    def _iter_sse_chunks(response):
        """解析 `data: {...}` 事件流；非 SSE 响应按单个 JSON 处理。"""
        content_type = (response.headers.get('Content-Type') or '').lower()
        if 'event-stream' not in content_type and 'stream' not in content_type:
            try:
                yield response.json()
                return
            except ValueError:
                payload = AntigravityClient._parse_json_lines(response)
                for chunk in payload:
                    yield chunk
            return

        for raw_line in response.iter_lines(decode_unicode=False):
            if not raw_line:
                continue
            try:
                line = raw_line.decode('utf-8', 'replace')
            except AttributeError:
                line = raw_line
            line = line.strip()
            if not line.startswith('data:'):
                continue
            payload = line[5:].strip()
            if not payload or payload == '[DONE]':
                continue
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                logger.debug(f"忽略无法解析的 SSE 分片: {payload[:120]}")

    @staticmethod
    def _parse_json_lines(response):
        text = response.text or ''
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else [parsed]
        except ValueError:
            chunks = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    chunks.append(json.loads(line))
                except ValueError:
                    continue
            if chunks:
                return chunks
            raise AntigravityError(f"Không thể phân tích phản hồi Antigravity: {text[:300]}")

    @staticmethod
    def _chunk_error(chunk: dict) -> Optional[dict]:
        error = chunk.get('error')
        if isinstance(error, dict):
            return error
        return None

    @staticmethod
    def _format_chunk_error(error: dict) -> str:
        code = error.get('code')
        message = error.get('message') or error.get('status') or json.dumps(error)[:200]
        return f"{code}: {message}" if code else str(message)

    def _raise_for_chunk_error(self, error: dict) -> None:
        """内联错误：配额类抛 AntigravityQuotaError（可换号），其余按普通失败。"""
        text = self._format_chunk_error(error)
        status = error.get('code')
        status = int(status) if isinstance(status, (int, float)) else None
        if status == 429 or looks_like_quota_error(text, status):
            raise AntigravityQuotaError(
                f"❌ Antigravity 生成中断（配额）: {text}\n"
                "【Giải pháp】多账号池会自动切换；单账号请稍后重试或换模型。",
                detail=error,
            )
        raise AntigravityError(
            f"❌ Quá trình tạo nội dung Antigravity bị gián đoạn: {text}\n"
            "【Giải pháp】Cửa sổ hạn mức 配额 có thể đã dùng hết, vui lòng thử lại sau hoặc đổi model khác."
        )

    def _collect_stream_text(self, response) -> str:
        texts: List[str] = []
        finish_reason = None
        for chunk in self._iter_sse_chunks(response):
            error = self._chunk_error(chunk)
            if error:
                self._raise_for_chunk_error(error)
            payload = chunk.get('response') or chunk
            for candidate in payload.get('candidates') or []:
                for part in (candidate.get('content') or {}).get('parts') or []:
                    if part.get('thought'):
                        continue  # 思考摘要不计入正文
                    text = part.get('text')
                    if text:
                        texts.append(text)
                if candidate.get('finishReason'):
                    finish_reason = candidate['finishReason']

        if finish_reason and finish_reason.upper() in ('SAFETY', 'BLOCKLIST', 'RECITATION', 'PROHIBITED_CONTENT'):
            raise AntigravityError(
                f"⚠️ Nội dung bị bộ lọc chính sách an toàn chặn ({finish_reason}).\n"
                "【Giải pháp】Điều chỉnh lại prompt rồi thử lại."
            )
        return ''.join(texts)

    # ---------- 图片 ----------

    def generate_image(
        self,
        prompt: str,
        model: str = None,
        aspect_ratio: str = '3:4',
        reference_images: List[bytes] = None,
        candidate_count: int = 1,
    ) -> List[bytes]:
        model_id = self.resolve_image_model(model)
        parts = []
        for image in reference_images or []:
            if isinstance(image, (bytes, bytearray)):
                from ..utils.image_compressor import compress_image

                compressed = compress_image(image, max_size_kb=200)
                parts.append({
                    'inlineData': {
                        'mimeType': 'image/png',
                        'data': base64.b64encode(compressed).decode('utf-8'),
                    }
                })
        parts.append({'text': prompt})

        generation_config = {
            'responseModalities': ['IMAGE'],
            'candidateCount': int(candidate_count),
        }
        if aspect_ratio:
            generation_config['imageConfig'] = {'aspectRatio': aspect_ratio}

        envelope = self._build_envelope(
            model=model_id,
            contents=[{'role': 'user', 'parts': parts}],
            system_instruction=IMAGE_SYSTEM_INSTRUCTION,
            generation_config=generation_config,
            session_seed=prompt,
        )
        envelope['request']['safetySettings'] = SAFETY_SETTINGS

        return self._request(
            STREAM_GENERATE_CONTENT_PATH,
            envelope,
            consume=self._collect_images,
            stream=True,
        )

    def _collect_images(self, response) -> List[bytes]:
        images: List[bytes] = []
        texts: List[str] = []
        finish_reason = None
        for chunk in self._iter_sse_chunks(response):
            error = self._chunk_error(chunk)
            if error:
                self._raise_for_chunk_error(error)
            payload = chunk.get('response') or chunk
            for candidate in payload.get('candidates') or []:
                for part in (candidate.get('content') or {}).get('parts') or []:
                    inline = part.get('inlineData') or {}
                    data = inline.get('data')
                    if data and inline.get('mimeType'):
                        try:
                            images.append(base64.b64decode(data))
                        except (ValueError, TypeError) as exc:
                            logger.warning(f"图片 base64 解码失败: {exc}")
                    elif part.get('text'):
                        texts.append(part['text'])
                if candidate.get('finishReason'):
                    finish_reason = candidate['finishReason']

        if not images:
            reason_note = f"（finishReason={finish_reason}）" if finish_reason else ''
            text_note = ('模型回复: ' + ' '.join(texts)[:200]) if texts else '模型没有返回图片。'
            raise AntigravityError(
                f"⚠️ Antigravity 未返回图片 {reason_note}\n"
                f"{text_note}\n"
                "【Giải pháp】\n"
                "1. Xác nhận model là model hình ảnh (ví dụ gemini-3-pro-image / gemini-3.1-flash-image)\n"
                "2. Đơn giản hóa prompt hoặc đổi model hình ảnh khác\n"
                "3. Hạn mức 配额 ảnh của gói miễn phí có thể đã dùng hết, vui lòng thử lại sau"
            )
        return images

    def resolve_image_model(self, model: str = None) -> str:
        """配置里写 auto/留空时，用账号实际提供的 imageGenerationModelIds。"""
        requested = (model or '').strip()
        if requested and requested.lower() not in ('auto', 'antigravity', 'default'):
            return strip_effort_annotation(requested)

        if self._resolved_image_model:
            return self._resolved_image_model

        try:
            discovered = self.fetch_image_models()
        except (AntigravityError, AntigravityAuthError) as exc:
            logger.warning(f"Antigravity 图片模型发现失败，回退默认模型: {exc}")
            return DEFAULT_IMAGE_MODEL
        if not discovered:
            return DEFAULT_IMAGE_MODEL
        self._resolved_image_model = discovered[0]
        logger.info(f"Antigravity 自动选择图片模型: {self._resolved_image_model}")
        return self._resolved_image_model

    def fetch_image_models(self) -> List[str]:
        roster = self.fetch_available_models()
        ids = [mid for mid in roster.get('image_model_ids') if mid]
        if ids:
            return ids
        return [mid for mid in IMAGE_MODELS if mid in roster.get('models', {})]

    # ---------- 发现 / 配额 ----------

    def fetch_available_models(self) -> dict:
        """POST /v1internal:fetchAvailableModels（需要 UA，否则 403）。"""
        response = self._post(FETCH_AVAILABLE_MODELS_PATH, {}, timeout=30)
        try:
            payload = response.json()
        except ValueError:
            raise AntigravityError("fetchAvailableModels 响应不是 JSON")

        models = payload.get('models') or {}
        enabled = {}
        for model_id, meta in models.items():
            if not isinstance(meta, dict):
                continue
            if model_id in ('chat_20706', 'chat_23310', 'gemini-2.5-pro'):
                continue
            if meta.get('isInternal') is True or meta.get('disabled') is True:
                continue
            enabled[model_id] = meta

        image_ids = [mid for mid in (payload.get('imageGenerationModelIds') or []) if isinstance(mid, str) and mid]
        text_ids = [mid for mid in enabled if mid not in image_ids]
        return {
            'models': enabled,
            'text_models': sorted(text_ids),
            'image_model_ids': image_ids,
        }

    def quota_summary(self) -> dict:
        """
        POST /v1internal:retrieveUserQuotaSummary
        结构：groups[].buckets[]：bucketId / remainingFraction / resetTime / disabled
        （不存在 modelQuotas 字段）
        """
        try:
            response = self._post(RETRIEVE_QUOTA_PATH, {}, timeout=30)
            payload = response.json()
        except AntigravityError as exc:
            logger.warning(f"读取 Antigravity 配额失败: {exc}")
            return {'groups': [], 'error': str(exc)}
        except ValueError:
            return {'groups': [], 'error': '配额响应不是 JSON'}

        groups = []
        for group in payload.get('groups') or []:
            buckets = []
            for bucket in group.get('buckets') or []:
                if not isinstance(bucket, dict):
                    continue
                buckets.append({
                    'bucket_id': bucket.get('bucketId'),
                    'remaining_fraction': bucket.get('remainingFraction'),
                    'reset_time': bucket.get('resetTime'),
                    'disabled': bucket.get('disabled'),
                })
            groups.append({'group_id': group.get('groupId') or group.get('id'), 'buckets': buckets})
        return {'groups': groups}


__all__ = ['AntigravityClient', 'AntigravityError', 'AntigravityQuotaError', 'derive_session_id']
