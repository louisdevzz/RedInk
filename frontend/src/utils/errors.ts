import axios from 'axios'

export interface AppError {
  type: string
  code: string
  title: string
  detail: string
  suggestion: string
  status: number
  retryable: boolean
  diagnostics?: Record<string, any>
}

export type ErrorLike = AppError | string | Error | unknown

export function normalizeApiError(error: ErrorLike, fallbackTitle = 'Thao tác thất bại'): AppError {
  if (isAppError(error)) return error

  if (axios.isAxiosError(error)) {
    const data = error.response?.data
    if (isAppError(data?.error)) return data.error
    if (typeof data?.error === 'string') {
      return legacyMessageToError(data.error, data.error_message || fallbackTitle, error.response?.status)
    }
    if (typeof data?.error_message === 'string') {
      return legacyMessageToError(data.error_message, fallbackTitle, error.response?.status)
    }
    if (!error.response) {
      return legacyMessageToError(
        error.message || 'Kết nối mạng thất bại',
        'Kết nối mạng thất bại',
        0,
        'Vui lòng xác nhận dịch vụ backend đã khởi động và kiểm tra kết nối mạng của trình duyệt.'
      )
    }
    return legacyMessageToError(error.message, fallbackTitle, error.response.status)
  }

  const maybeData = error as any
  if (isAppError(maybeData?.error)) return maybeData.error
  if (typeof maybeData?.error === 'string') {
    return legacyMessageToError(maybeData.error, maybeData.error_message || fallbackTitle, maybeData.status)
  }
  if (typeof maybeData?.message === 'string') {
    return legacyMessageToError(maybeData.message, fallbackTitle, maybeData.status)
  }
  if (typeof error === 'string') {
    return legacyMessageToError(error, fallbackTitle)
  }

  return legacyMessageToError('Vui lòng thử lại sau; nếu tiếp tục thất bại, vui lòng sao chép thông tin chẩn đoán để phản hồi.', fallbackTitle)
}

export function formatErrorMessage(error: ErrorLike, fallbackTitle = 'Thao tác thất bại'): string {
  const appError = normalizeApiError(error, fallbackTitle)
  return `${appError.title}：${appError.suggestion || appError.detail}`
}

export function diagnosticsText(error: AppError): string {
  const payload = {
    code: error.code,
    title: error.title,
    detail: error.detail,
    suggestion: error.suggestion,
    status: error.status,
    retryable: error.retryable,
    diagnostics: error.diagnostics || {}
  }
  return JSON.stringify(payload, null, 2)
}

function isAppError(value: any): value is AppError {
  return !!value
    && typeof value === 'object'
    && typeof value.code === 'string'
    && typeof value.title === 'string'
    && typeof value.detail === 'string'
}

function legacyMessageToError(
  message: string,
  fallbackTitle = 'Thao tác thất bại',
  status = 500,
  suggestion?: string
): AppError {
  const clean = message || 'Đã xảy ra lỗi không xác định'
  const firstLine = clean.split('\n').map(line => line.trim()).find(Boolean) || fallbackTitle

  return {
    type: `https://redink.app/errors/${inferCode(clean).toLowerCase().replace(/_/g, '-')}`,
    code: inferCode(clean),
    title: inferTitle(clean, fallbackTitle),
    detail: firstLine,
    suggestion: suggestion || inferSuggestion(clean),
    status: status || 500,
    retryable: inferRetryable(clean),
    diagnostics: {
      raw: clean
    }
  }
}

function inferCode(message: string): string {
  const text = message.toLowerCase()
  if (text.includes('proxyerror') || (text.includes('connection refused') && text.includes('127.0.0.1'))) return 'PROXY_UNAVAILABLE'
  if (text.includes('ssleoferror') || text.includes('unexpected_eof') || text.includes('198.18.')) return 'NETWORK_FAKE_IP_TLS'
  if (message.includes('不支持 /v1/chat/completions')) return 'MODEL_ENDPOINT_MISMATCH'
  if (text.includes('405') || text.includes('method not allowed') || text.includes('not allowed')) return 'ENDPOINT_METHOD_MISMATCH'
  if (text.includes('401') || text.includes('403') || text.includes('unauthorized') || text.includes('forbidden')) return 'AUTH_OR_PERMISSION'
  if (text.includes('429') || text.includes('rate') || message.includes('配额') || message.includes('限流')) return 'RATE_LIMITED'
  if (text.includes('timeout') || message.includes('超时')) return 'NETWORK_TIMEOUT'
  if (message.includes('不存在') || text.includes('not found')) return 'RESOURCE_NOT_FOUND'
  return 'UNKNOWN_ERROR'
}

function inferTitle(message: string, fallbackTitle: string): string {
  const code = inferCode(message)
  const titles: Record<string, string> = {
    PROXY_UNAVAILABLE: 'Proxy cục bộ không khả dụng',
    NETWORK_FAKE_IP_TLS: 'Kết nối proxy mạng bất thường',
    MODEL_ENDPOINT_MISMATCH: 'Mô hình và giao diện không khớp',
    ENDPOINT_METHOD_MISMATCH: 'Đường dẫn giao diện hoặc phương thức yêu cầu không khớp',
    AUTH_OR_PERMISSION: 'API Key hoặc quyền hạn không khả dụng',
    RATE_LIMITED: 'Giới hạn lưu lượng thượng nguồn hoặc không đủ hạn mức',
    NETWORK_TIMEOUT: 'Yêu cầu mạng quá thời gian chờ',
    RESOURCE_NOT_FOUND: 'Tài nguyên không tồn tại',
    UNKNOWN_ERROR: fallbackTitle
  }
  return titles[code] || fallbackTitle
}

function inferSuggestion(message: string): string {
  const code = inferCode(message)
  const suggestions: Record<string, string> = {
    PROXY_UNAVAILABLE: 'Vui lòng xác nhận ứng dụng proxy đã bật và cổng proxy HTTP/HTTPS đang lắng nghe.',
    NETWORK_FAKE_IP_TLS: 'Vui lòng xác nhận ứng dụng proxy đã bật hoặc tắt Fake-IP DNS rồi thử lại.',
    MODEL_ENDPOINT_MISMATCH: 'Vui lòng chuyển sang mô hình hỗ trợ giao diện hiện tại trong phần cài đặt hoặc điều chỉnh endpoint_type của nhà cung cấp.',
    ENDPOINT_METHOD_MISMATCH: 'Vui lòng xác nhận nhà cung cấp này hỗ trợ giao diện tương thích OpenAI, đồng thời kiểm tra Base URL, đường dẫn /v1 và endpoint_type.',
    AUTH_OR_PERMISSION: 'Vui lòng kiểm tra API Key, quyền truy cập mô hình, số dư tài khoản và cấu hình nhà cung cấp.',
    RATE_LIMITED: 'Vui lòng thử lại sau hoặc giảm số lượng tác vụ đồng thời / kiểm tra hạn mức tài khoản.',
    NETWORK_TIMEOUT: 'Vui lòng kiểm tra mạng, proxy và trạng thái nhà cung cấp rồi thử lại.',
    RESOURCE_NOT_FOUND: 'Vui lòng quay lại danh sách và làm mới rồi thử lại.',
    UNKNOWN_ERROR: 'Vui lòng thử lại sau; nếu tiếp tục thất bại, vui lòng sao chép thông tin chẩn đoán để phản hồi.'
  }
  return suggestions[code] || suggestions.UNKNOWN_ERROR
}

function inferRetryable(message: string): boolean {
  const code = inferCode(message)
  return ['PROXY_UNAVAILABLE', 'NETWORK_FAKE_IP_TLS', 'RATE_LIMITED', 'NETWORK_TIMEOUT', 'UNKNOWN_ERROR'].includes(code)
}
