"""统一 API 错误模型和分类器。用户可见文案使用中文 + 越南语关键词匹配，输出为越南语。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple, Union


ERROR_TYPE_BASE = "https://redink.app/errors"


@dataclass
class AppError:
    code: str
    title: str
    detail: str
    suggestion: str
    status: int = 500
    retryable: bool = False
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    @property
    def type(self) -> str:
        return f"{ERROR_TYPE_BASE}/{self.code.lower().replace('_', '-')}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "code": self.code,
            "title": self.title,
            "detail": self.detail,
            "suggestion": self.suggestion,
            "status": self.status,
            "retryable": self.retryable,
            "diagnostics": self.diagnostics,
        }

    def to_message(self) -> str:
        if self.suggestion:
            return f"{self.title}: {self.suggestion}"
        return f"{self.title}: {self.detail}"


def error_payload(error: Union[AppError, Exception, str], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    app_error = ensure_app_error(error, context=context)
    return {
        "success": False,
        "error": app_error.to_dict(),
        "error_message": app_error.to_message(),
    }


def ensure_app_error(error: Union[AppError, Exception, str], context: Optional[Dict[str, Any]] = None) -> AppError:
    if isinstance(error, AppError):
        if context:
            diagnostics = dict(error.diagnostics)
            diagnostics.update(_sanitize_diagnostics(context))
            return AppError(
                code=error.code,
                title=error.title,
                detail=error.detail,
                suggestion=error.suggestion,
                status=error.status,
                retryable=error.retryable,
                diagnostics=diagnostics,
            )
        return error
    return classify_error(error, context=context)


def classify_error(error: Union[Exception, str], context: Optional[Dict[str, Any]] = None) -> AppError:
    raw = str(error)
    text = raw.lower()
    diagnostics = _sanitize_diagnostics(context or {})
    diagnostics["raw"] = _summarize_raw(raw, 1000)

    status, upstream_message = _extract_status_and_message(raw)
    if status:
        diagnostics["http_status"] = status
    if upstream_message:
        diagnostics["upstream_message"] = _truncate(upstream_message, 500)

    host = _extract_host(raw) or diagnostics.get("base_url") or diagnostics.get("host")
    endpoint = diagnostics.get("endpoint") or _extract_endpoint(raw)

    # 配置缺失（未填 API Key / 未登录 Google 账号）——属于可自助修复的配置问题，
    # 不能落到 UNKNOWN_ERROR + retryable，否则用户会反复重试同一个错误。
    if _looks_like_missing_api_key(raw):
        return AppError(
            code="API_KEY_MISSING",
            title="Thiếu API Key",
            detail=upstream_message or _first_meaningful_line(raw) or "Nhà cung cấp chưa được cấu hình API Key.",
            suggestion=(
                "Vào Cài đặt để nhập API Key cho nhà cung cấp này, hoặc chuyển sang đăng nhập "
                "Google (Antigravity) để dùng không cần API Key."
            ),
            status=400,
            retryable=False,
            diagnostics=diagnostics,
        )

    if _looks_like_missing_login(raw):
        return AppError(
            code="GOOGLE_SIGNIN_REQUIRED",
            title="Chưa đăng nhập Google",
            detail=upstream_message or _first_meaningful_line(raw) or "Nhà cung cấp Antigravity cần đăng nhập Google.",
            suggestion="Vào Cài đặt → mục 'Đăng nhập Google (Antigravity)' và hoàn tất đăng nhập; không cần API Key.",
            status=400,
            retryable=False,
            diagnostics=diagnostics,
        )

    if _looks_like_missing_provider_config(raw):
        return AppError(
            code="PROVIDER_NOT_CONFIGURED",
            title="Chưa cấu hình nhà cung cấp",
            detail=upstream_message or _first_meaningful_line(raw) or "Không tìm thấy cấu hình nhà cung cấp hợp lệ.",
            suggestion="Vào Cài đặt để thêm nhà cung cấp và chọn nhà cung cấp đang hoạt động.",
            status=400,
            retryable=False,
            diagnostics=diagnostics,
        )

    if _looks_like_proxy_refused(text):
        return AppError(
            code="PROXY_UNAVAILABLE",
            title="Proxy cục bộ không khả dụng",
            detail="Yêu cầu cần đi qua proxy cục bộ nhưng cổng proxy không phản hồi.",
            suggestion="Kiểm tra ứng dụng proxy đang bật và cổng HTTP/HTTPS đang lắng nghe; nếu không dùng proxy hãy tắt biến môi trường liên quan.",
            status=400,
            retryable=True,
            diagnostics=diagnostics,
        )

    if _looks_like_fake_ip_tls(text):
        detail = f"Không thể kết nối ổn định tới {host}" if host else "Kết nối TLS với máy chủ bị ngắt khi bắt tay."
        return AppError(
            code="NETWORK_FAKE_IP_TLS",
            title="Kết nối proxy/network bất thường",
            detail=detail,
            suggestion="Hãy chắc chắn ứng dụng proxy đang bật, hoặc tắt chế độ Fake-IP DNS rồi thử lại.",
            status=400,
            retryable=True,
            diagnostics=diagnostics,
        )

    if "不支持 /v1/chat/completions" in raw or (
        "not support" in text and "chat/completions" in text
    ) or (
        "unsupported" in text and "chat/completions" in text
    ):
        return AppError(
            code="MODEL_ENDPOINT_MISMATCH",
            title="Model không khớp với endpoint",
            detail=upstream_message or "Model hiện tại không hỗ trợ giao thức OpenAI Chat Completions.",
            suggestion="Chọn model chữ khác có hỗ trợ giao thức này trong Cài đặt, hoặc chỉnh endpoint_type của nhà cung cấp.",
            status=400,
            retryable=False,
            diagnostics=diagnostics,
        )

    if "unknown parameter" in text and "response_format" in text:
        return AppError(
            code="UPSTREAM_PARAM_UNSUPPORTED",
            title="Tham số không được hỗ trợ",
            detail="Nhà cung cấp không hỗ trợ tham số response_format.",
            suggestion="Hãy xoá response_format trong cấu hình nhà cung cấp, hoặc dùng phiên bản đã tự động hạ cấp.",
            status=400,
            retryable=True,
            diagnostics=diagnostics,
        )

    if status in (400, 415) and (
        "json" in text
        or "unsupported media type" in text
        or "bad request" in text
        or "content-type" in text
    ):
        return AppError(
            code="INVALID_REQUEST",
            title="Định dạng yêu cầu không hợp lệ",
            detail=upstream_message or "Nội dung yêu cầu không phải JSON hợp lệ, hoặc Content-Type không đúng.",
            suggestion="Hãy tải lại trang rồi thử lại; nếu gọi API trực tiếp, dùng application/json và JSON hợp lệ.",
            status=400,
            retryable=False,
            diagnostics=diagnostics,
        )

    if status == 405 or "405 not allowed" in text or "method not allowed" in text:
        endpoint_text = f" {endpoint}" if endpoint else ""
        return AppError(
            code="ENDPOINT_METHOD_MISMATCH",
            title="Sai đường dẫn endpoint hoặc phương thức",
            detail=f"Máy chủ không chấp nhận yêu cầu hiện tại{endpoint_text}, thường do Base URL hoặc endpoint_type.",
            suggestion="Xác nhận nhà cung cấp hỗ trợ giao thức OpenAI-compatible và kiểm tra Base URL, đường dẫn /v1, endpoint_type theo tài liệu.",
            status=400,
            retryable=False,
            diagnostics=diagnostics,
        )

    if status in (401, 403) or any(keyword in text for keyword in [
        "unauthorized",
        "unauthenticated",
        "forbidden",
        "invalid api key",
        "api key 认证失败",
        "权限被拒绝",
    ]):
        return AppError(
            code="AUTH_OR_PERMISSION",
            title="API Key hoặc quyền không khả dụng",
            detail=upstream_message or "Nhà cung cấp đã từ chối yêu cầu.",
            suggestion="Kiểm tra API Key, quyền tài khoản, quyền truy cập model và số dư; hoặc đăng nhập Google (Antigravity).",
            status=401 if status == 401 else 403,
            retryable=False,
            diagnostics=diagnostics,
        )

    if status == 429 or any(keyword in text for keyword in ["rate limit", "resource_exhausted", "quota", "配额", "限流"]):
        return AppError(
            code="RATE_LIMITED",
            title="Bị giới hạn tần suất hoặc hết hạn mức",
            detail=upstream_message or "Máy chủ tạm thời từ chối vì quá nhiều yêu cầu.",
            suggestion="Thử lại sau ít phút, giảm song song, hoặc kiểm tra hạn mức tài khoản.",
            status=429,
            retryable=True,
            diagnostics=diagnostics,
        )

    if status == 404 or ("model" in text and ("not found" in text or "不存在" in raw)):
        return AppError(
            code="MODEL_NOT_FOUND",
            title="Không tìm thấy model hoặc endpoint",
            detail=upstream_message or "Tên model hoặc địa chỉ endpoint hiện không truy cập được.",
            suggestion="Kiểm tra Base URL, endpoint_type và tên model (với Antigravity cần dùng id model gốc, không có hậu tố trong ngoặc).",
            status=404,
            retryable=False,
            diagnostics=diagnostics,
        )

    if status and status >= 500:
        return AppError(
            code="UPSTREAM_UNAVAILABLE",
            title="Máy chủ lỗi",
            detail=upstream_message or f"Nhà cung cấp trả về HTTP {status}.",
            suggestion="Thử lại sau; nếu vẫn lỗi hãy đổi nhà cung cấp hoặc kiểm tra tình trạng dịch vụ.",
            status=502,
            retryable=True,
            diagnostics=diagnostics,
        )

    if _looks_like_timeout(text):
        return AppError(
            code="NETWORK_TIMEOUT",
            title="Yêu cầu mạng quá thời gian",
            detail=f"Kết nối tới {host} quá thời gian." if host else "Yêu cầu chờ quá lâu.",
            suggestion="Kiểm tra mạng, proxy và tình trạng nhà cung cấp rồi thử lại.",
            status=504,
            retryable=True,
            diagnostics=diagnostics,
        )

    if _looks_like_network(text):
        return AppError(
            code="NETWORK_ERROR",
            title="Kết nối mạng thất bại",
            detail=f"Không thể kết nối tới {host}." if host else "Không thể kết nối tới máy chủ.",
            suggestion="Kiểm tra kết nối mạng, cấu hình proxy và Base URL.",
            status=502,
            retryable=True,
            diagnostics=diagnostics,
        )

    if "历史记录不存在" in raw or "任务不存在" in raw or "历史记录未找到" in raw:
        return AppError(
            code="RESOURCE_NOT_FOUND",
            title="Không tìm thấy dữ liệu",
            detail=_first_meaningful_line(raw),
            suggestion="Quay lại danh sách lịch sử, tải lại trang rồi thử lại.",
            status=404,
            retryable=False,
            diagnostics=diagnostics,
        )

    if "不存在" in raw or "not found" in text:
        return AppError(
            code="RESOURCE_NOT_FOUND",
            title="Không tìm thấy dữ liệu",
            detail=_first_meaningful_line(raw),
            suggestion="Kiểm tra lại mục đang thao tác rồi thử lại.",
            status=404,
            retryable=False,
            diagnostics=diagnostics,
        )

    return AppError(
        code="UNKNOWN_ERROR",
        title="Thao tác thất bại",
        detail=_first_meaningful_line(raw) or "Đã xảy ra lỗi không xác định.",
        suggestion="Thử lại sau; nếu lỗi vẫn xảy ra hãy sao chép thông tin chẩn đoán để phản hồi.",
        status=500,
        retryable=True,
        diagnostics=diagnostics,
    )


def _looks_like_missing_api_key(raw: str) -> bool:
    lowered = raw.lower()
    return any(token in lowered for token in [
        "未配置 api key",
        "未配置 api_key",
        "api key 未配置",
        "api key not configured",
        "请填写 api key",
        "chưa được cấu hình api key",
        "chưa cấu hình api key",
        "chưa có api key",
        "thiếu api key",
        "missing api key",
    ])


def _looks_like_missing_login(raw: str) -> bool:
    lowered = raw.lower()
    return (
        "尚未登录" in raw
        or "未登录 google" in raw
        or "需要重新登录" in raw
        or "google 登录已失效" in raw
        or "chưa đăng nhập" in lowered
        or "chưa login" in lowered
        or "đăng nhập google" in lowered and "chưa" in lowered
        or "please log in" in lowered
    )


def _looks_like_missing_provider_config(raw: str) -> bool:
    lowered = raw.lower()
    return (
        "未找到任何" in raw
        or "未找到服务商配置" in raw
        or "未找到图片生成服务商配置" in raw
        or "未找到文本生成服务商配置" in raw
        or "不支持的图片生成服务商" in raw
        or "not configured provider" in lowered
    )
def _sanitize_diagnostics(data: Dict[str, Any]) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {}
    for key, value in data.items():
        if value is None:
            continue
        if "api_key" in key.lower() or "authorization" in key.lower():
            sanitized[key] = "***"
        elif isinstance(value, (str, int, float, bool)):
            sanitized[key] = _truncate(str(value), 500) if isinstance(value, str) else value
        else:
            sanitized[key] = _truncate(str(value), 500)
    return sanitized


def _extract_status_and_message(raw: str) -> Tuple[Optional[int], Optional[str]]:
    status = None
    status_match = re.search(r"(?:HTTP|状态码|Trạng thái)\s*[:：]?\s*(\d{3})", raw, re.IGNORECASE)
    if status_match:
        status = int(status_match.group(1))
    else:
        leading_status_match = re.search(r"^\s*(\d{3})\s+[A-Za-z]", raw)
        if leading_status_match:
            status = int(leading_status_match.group(1))

    json_match = re.search(r"(\{.*\})", raw, re.DOTALL)
    if json_match:
        try:
            payload = json.loads(json_match.group(1))
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                message = error.get("message") or error.get("detail")
                if message:
                    return status, str(message)
            if isinstance(error, str):
                return status, error
        except Exception:
            pass

    original_match = re.search(r"【原始错误】\s*([\s\S]+)", raw)
    if not original_match:
        original_match = re.search(r"Lỗi gốc:\s*([\s\S]+)", raw)
    if original_match:
        return status, _first_meaningful_line(original_match.group(1))

    if "<html" in raw.lower() or "<!doctype" in raw.lower():
        return status, _summarize_html(raw)

    return status, None


def _extract_host(raw: str) -> Optional[str]:
    match = re.search(r"host='([^']+)'", raw)
    if match:
        return match.group(1)
    match = re.search(r"https?://([^/\s)]+)", raw)
    if match:
        return match.group(1)
    return None


def _extract_endpoint(raw: str) -> Optional[str]:
    match = re.search(r"url:\s*(/[^\s)]+)", raw, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"with url:\s*(/[^\s)]+)", raw, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"https?://[^/]+(/[^\s)]+)", raw)
    if match:
        return match.group(1)
    return None


def _looks_like_proxy_refused(text: str) -> bool:
    return (
        "proxyerror" in text
        and ("connection refused" in text or "failed to establish a new connection" in text)
    ) or (
        "127.0.0.1" in text and "7890" in text and "connection refused" in text
    )


def _looks_like_fake_ip_tls(text: str) -> bool:
    return (
        "ssleoferror" in text
        or "unexpected_eof_while_reading" in text
        or "ssl_error_syscall" in text
        or ("eof occurred" in text and "ssl" in text)
        or ("198.18." in text and "ssl" in text)
    )


def _looks_like_timeout(text: str) -> bool:
    return "timeout" in text or "timed out" in text or "超时" in text or "quá thời gian" in text


def _looks_like_network(text: str) -> bool:
    return any(keyword in text for keyword in [
        "connectionerror",
        "connection aborted",
        "connection reset",
        "failed to establish a new connection",
        "network",
        "网络",
        "连接失败",
        "连接错误",
        "kết nối mạng",
    ])


def _first_meaningful_line(value: str) -> str:
    for line in value.splitlines():
        stripped = line.strip(" \t\r\n-❌⚠️⏳🌐🔒")
        if stripped:
            return stripped
    return ""


def _summarize_raw(value: str, limit: int) -> str:
    if "<html" in value.lower() or "<!doctype" in value.lower():
        return _truncate(_summarize_html(value), limit)
    return _truncate(value, limit)


def _summarize_html(value: str) -> str:
    text = value.replace("\\r", " ").replace("\\n", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or _first_meaningful_line(value)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."
