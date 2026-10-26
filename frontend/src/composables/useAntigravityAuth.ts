import { onUnmounted, ref } from 'vue'
import {
  cancelAntigravityLogin,
  getAntigravityAccounts,
  getAntigravityModels,
  getAntigravityStatus,
  logoutAntigravity,
  logoutAntigravityAccount,
  unblockAntigravityAccount,
  startAntigravityLogin,
  type AntigravityAccount,
  type AntigravityCredentialState,
  type AntigravityLoginStatus
} from '../api'
import { normalizeApiError, type AppError } from '../utils/errors'

/**
 * Google Antigravity（Google 账号登录）状态管理 Composable
 *
 * - startLogin：请求后端打开浏览器授权，并轮询状态直到 connected/failed
 *   （多账号：再次登录不会顶掉旧账号，而是加入账号池，上游 429 时自动轮换）
 * - refreshStatus：读取当前登录状态（页面加载时调用）
 * - logout：清除全部本地凭据；logoutAccount/remove：只退出单个账号
 * - loadModels：拉取该账号可用的文本 / 图片模型
 */

/**
 * Google Antigravity（Google 账号登录）状态管理 Composable
 *
 * - startLogin：请求后端打开浏览器授权，并轮询状态直到 connected/failed
 * - refreshStatus：读取当前登录状态（页面加载时调用）
 * - logout：清除本地凭据
 * - loadModels：拉取该账号可用的文本 / 图片模型
 */

export type AntigravityFlowState = 'idle' | 'waiting' | 'provisioning' | 'connected' | 'failed' | 'cancelled'

const POLL_INTERVAL_MS = 1500
const MAX_POLLS = 220 // ~5.5 分钟，与后端 300s 超时对齐

export function useAntigravityAuth() {
  const loading = ref(false)
  const loggingIn = ref(false)
  const connected = ref(false)
  const credential = ref<AntigravityCredentialState | null>(null)
  const flow = ref<AntigravityFlowState>('idle')
  const message = ref('')
  const authUrl = ref<string | null>(null)
  const error = ref<AppError | null>(null)

  const textModels = ref<string[]>([])
  const imageModels = ref<string[]>([])
  const modelsLoading = ref(false)
  const accounts = ref<AntigravityAccount[]>([])

  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let pollAttempts = 0

  function stopPolling() {
    if (pollTimer !== null) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
    pollAttempts = 0
  }

  function applyStatus(status: AntigravityLoginStatus) {
    connected.value = Boolean(status.connected || status.credential?.connected)
    credential.value = status.credential ?? null
    if (status.flow) flow.value = status.flow
    if (status.message) message.value = status.message
    if (status.auth_url) authUrl.value = status.auth_url
    if (status.error) error.value = normalizeApiError(status.error, 'Đăng nhập Google thất bại')
    accounts.value = status.credential?.accounts ?? []
    if (status.credential?.accounts === undefined) void refreshAccounts()
  }

  async function refreshAccounts() {
    try {
      const result = await getAntigravityAccounts()
      if (result.success) accounts.value = result.accounts ?? []
    } catch (e) {
      console.error('获取账号池失败:', e)
    }
  }

  async function logoutAccount(accountId: string) {
    error.value = null
    try {
      await logoutAntigravityAccount(accountId)
      await Promise.all([refreshStatus(), refreshAccounts()])
      message.value = 'Đã đăng xuất tài khoản đó'
    } catch (e) {
      error.value = normalizeApiError(e, 'Đăng xuất tài khoản thất bại')
    }
  }

  async function unblockAccount(accountId: string) {
    error.value = null
    try {
      await unblockAntigravityAccount(accountId)
      await Promise.all([refreshStatus(), refreshAccounts()])
      message.value = 'Đã khôi phục tài khoản'
    } catch (e) {
      error.value = normalizeApiError(e, 'Khôi phục tài khoản thất bại')
    }
  }

  async function refreshStatus() {
    loading.value = true
    try {
      applyStatus(await getAntigravityStatus())
    } catch (e) {
      error.value = normalizeApiError(e, 'Lấy trạng thái đăng nhập thất bại')
    } finally {
      loading.value = false
    }
  }

  async function startLogin() {
    error.value = null
    loggingIn.value = true
    try {
      const result = await startAntigravityLogin()
      if (!result.success) {
        throw new Error(result.message || 'Không thể khởi động đăng nhập Google')
      }
      flow.value = (result.state as AntigravityFlowState) || 'waiting'
      authUrl.value = result.auth_url ?? null
      message.value = result.message || 'Vui lòng hoàn tất đăng nhập Google trong trình duyệt'
      pollStatus()
    } catch (e) {
      error.value = normalizeApiError(e, 'Khởi động đăng nhập Google thất bại')
      loggingIn.value = false
    }
  }

  function pollStatus() {
    stopPolling()
    const tick = async () => {
      pollAttempts += 1
      try {
        const status = await getAntigravityStatus()
        applyStatus(status)

        if (status.flow === 'connected') {
          loggingIn.value = false
          message.value = status.message || 'Đăng nhập thành công'
          stopPolling()
          await loadModels()
          return
        }
        if (status.flow === 'failed' || status.flow === 'cancelled') {
          loggingIn.value = false
          if (status.error) error.value = normalizeApiError(status.error, 'Đăng nhập Google thất bại')
          stopPolling()
          return
        }
      } catch (e) {
        error.value = normalizeApiError(e, 'Kiểm tra trạng thái đăng nhập thất bại')
        loggingIn.value = false
        stopPolling()
        return
      }

      if (pollAttempts >= MAX_POLLS) {
        loggingIn.value = false
        message.value = 'Hết thời gian đăng nhập, vui lòng thử lại'
        stopPolling()
        return
      }
      pollTimer = setTimeout(tick, POLL_INTERVAL_MS)
    }
    pollTimer = setTimeout(tick, POLL_INTERVAL_MS)
  }

  async function cancelLogin() {
    try {
      await cancelAntigravityLogin()
    } finally {
      stopPolling()
      loggingIn.value = false
      flow.value = 'cancelled'
      message.value = 'Đã hủy đăng nhập'
    }
  }

  async function logout() {
    error.value = null
    try {
      await logoutAntigravity()
      connected.value = false
      credential.value = null
      flow.value = 'idle'
      message.value = 'Đã đăng xuất tài khoản Google'
      authUrl.value = null
      textModels.value = []
      imageModels.value = []
    } catch (e) {
      error.value = normalizeApiError(e, 'Đăng xuất thất bại')
    }
  }

  async function loadModels() {
    modelsLoading.value = true
    try {
      const result = await getAntigravityModels()
      if (result.success) {
        textModels.value = result.text_models ?? []
        imageModels.value = result.image_models ?? []
      }
    } catch (e) {
      // 模型清单只是辅助信息，失败不打断登录流程
      console.error('获取可用模型失败:', e)
    } finally {
      modelsLoading.value = false
    }
  }

  onUnmounted(() => stopPolling())

  return {
    loading,
    loggingIn,
    connected,
    credential,
    flow,
    message,
    authUrl,
    error,
    accounts,
    textModels,
    imageModels,
    modelsLoading,
    refreshStatus,
    refreshAccounts,
    startLogin,
    cancelLogin,
    logout,
    logoutAccount,
    unblockAccount,
    loadModels
  }
}
