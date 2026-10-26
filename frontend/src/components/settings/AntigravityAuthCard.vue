<template>
  <div class="auth-card card">
    <div class="section-header">
      <div>
        <h2 class="section-title">Đăng nhập Google 账号登录 (Antigravity)</h2>
        <p class="section-desc">
          Sau khi đăng nhập có thể dùng trực tiếp hạn ngạch tài khoản Google để tạo văn bản và hình ảnh, <strong>không cần API Key</strong>;
          phương thức API Key vẫn là tùy chọn. Có thể đăng nhập <strong>nhiều tài khoản</strong> — khi một tài khoản hết hạn mức,
          RedInk tự động chuyển sang tài khoản khác.
        </p>
      </div>

      <div class="auth-actions">
        <button
          class="btn btn-small"
          :class="{ 'btn-primary': !connected }"
          @click="$emit('login')"
          :disabled="loggingIn"
        >
          <span v-if="loggingIn" class="spinner-small"></span>
          {{ loggingIn ? 'Đang đăng nhập...' : (connected ? 'Thêm tài khoản Google' : 'Sử dụng Google 账号登录') }}
        </button>
        <button v-if="connected" class="btn btn-small" @click="onLogout">Đăng xuất tất cả</button>
      </div>
    </div>

    <div class="auth-status" :class="statusClass">
      <span class="status-dot"></span>
      <span class="status-text">{{ statusText }}</span>
    </div>

    <!-- 账号池：多账号轮换状态 -->
    <div v-if="accounts.length" class="auth-accounts">
      <label class="accounts-label">
        Tài khoản trong pool ({{ healthyCount }} khả dụng / {{ accounts.length }})
      </label>
      <table class="account-table">
        <thead>
          <tr>
            <th>Tài khoản</th>
            <th>Project</th>
            <th>Trạng thái</th>
            <th>Thao tác</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="account in accounts" :key="account.id">
            <td :title="account.id">{{ account.email || account.id }}</td>
            <td class="account-project">{{ account.project_id || '-' }}</td>
            <td>
              <span class="account-state" :class="accountStateClass(account)">
                {{ accountStateText(account) }}
              </span>
            </td>
            <td class="account-actions">
              <button
                v-if="account.cooling_down"
                class="btn btn-small"
                type="button"
                @click="$emit('unblock', account.id)"
                title="Đặt lại ngay thay vì chờ hết cooldown"
              >
                Khôi phục
              </button>
              <button
                class="btn btn-small btn-danger"
                type="button"
                @click="onRemoveAccount(account)"
                title="Đăng xuất tài khoản này"
              >
                Xóa
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="recoveryHint" class="accounts-hint">{{ recoveryHint }}</p>
    </div>

    <p v-if="!connected && authUrl" class="auth-fallback">
      Trình duyệt không tự động mở?
      <a :href="authUrl" target="_blank" rel="noopener noreferrer">Nhấn vào đây để hoàn tất đăng nhập thủ công</a>
    </p>

    <div v-if="connected && (textModels.length || imageModels.length)" class="auth-models">
      <div class="model-group" v-if="textModels.length">
        <label>Mô hình văn bản khả dụng ({{ textModels.length }})</label>
        <div class="chip-row">
          <button
            v-for="model in textModels"
            :key="model"
            class="model-chip"
            type="button"
            @click="copyModel(model)"
            :title="'Nhấn để sao chép ' + model"
          >
            {{ model }}
          </button>
        </div>
      </div>
      <div class="model-group" v-if="imageModels.length">
        <label>Mô hình hình ảnh khả dụng ({{ imageModels.length }})</label>
        <div class="chip-row">
          <button
            v-for="model in imageModels"
            :key="model"
            class="model-chip"
            type="button"
            @click="copyModel(model)"
            :title="'Nhấn để sao chép ' + model"
          >
            {{ model }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="loggingIn" class="auth-waiting">
      <button class="btn btn-small" @click="$emit('cancel')">Hủy đăng nhập</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { AntigravityAccount, AntigravityCredentialState } from '../../api'
import type { AntigravityFlowState } from '../../composables/useAntigravityAuth'

/**
 * Google 账号登录卡片（多账号池）
 *
 * 登录状态与请求由 useAntigravityAuth 管理，本组件只负责展示与交互。
 * 每个 Google 账号一行：显示可用性（健康 / 配额冷却 / 已失效），
 * 支持单账号退出与手动解除冷却。
 */

interface Props {
  connected: boolean
  loggingIn: boolean
  flow: AntigravityFlowState
  message: string
  authUrl: string | null
  credential: AntigravityCredentialState | null
  accounts: AntigravityAccount[]
  textModels: string[]
  imageModels: string[]
  modelsLoading: boolean
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'login'): void
  (e: 'logout'): void
  (e: 'cancel'): void
  (e: 'logoutAccount', accountId: string): void
  (e: 'unblock', accountId: string): void
}>()

const healthyCount = computed(
  () => props.accounts.filter(a => a.connected && !a.cooling_down && !a.dead).length
)

const statusText = computed(() => {
  if (props.connected) {
    if (props.message) return props.message
    const n = props.accounts.length
    return n > 1
      ? `Đã đăng nhập ${n} tài khoản Google, tự động chuyển tài khoản khi một tài khoản hết hạn mức (không cần API Key)`
      : 'Đã đăng nhập Google 账号登录, có thể dùng trực tiếp (không cần API Key)'
  }
  switch (props.flow) {
    case 'waiting':
      return props.message || 'Đang chờ hoàn tất Google 账号登录 trên trình duyệt...'
    case 'provisioning':
      return props.message || 'Đang kích hoạt Cloud Code Assist...'
    case 'failed':
      return props.message || 'Đăng nhập thất bại, vui lòng thử lại'
    case 'cancelled':
      return 'Đã hủy đăng nhập'
    default:
      return props.accounts.some(a => a.dead || a.cooling_down)
        ? 'Tất cả tài khoản Google đều đang hết hạn mức hoặc đã đăng xuất'
        : 'Chưa đăng nhập Google 账号登录'
  }
})

const statusClass = computed(() => {
  if (props.connected) return 'ok'
  if (props.flow === 'waiting' || props.flow === 'provisioning') return 'pending'
  if (props.flow === 'failed' || props.accounts.some(a => a.cooling_down || a.dead)) return 'error'
  return 'idle'
})

const recoveryHint = computed(() => {
  if (props.connected || !props.accounts.length) return ''
  const until = Math.max(...props.accounts.map(a => a.blocked_until || 0), 0)
  if (!until || until <= Date.now()) return ''
  const minutes = Math.ceil((until - Date.now()) / 60_000)
  return `Hạn mức sẽ tự hồi phục sau khoảng ${minutes} phút; hoặc bấm "Khôi phục" để thử lại ngay, hoặc "Thêm tài khoản Google" để bổ sung pool.`
})

function accountStateText(account: AntigravityAccount): string {
  if (account.dead) return 'Đã hết hiệu lực (cần đăng nhập lại)'
  if (account.cooling_down) {
    const minutes = account.blocked_until
      ? Math.max(1, Math.ceil((account.blocked_until - Date.now()) / 60_000))
      : null
    return minutes ? `Đang cooldown hạn mức (~${minutes} phút)` : 'Đang cooldown hạn mức'
  }
  if (account.can_refresh === false) return 'Sắp hết hạn, vui lòng đăng nhập lại'
  return 'Khả dụng'
}

function accountStateClass(account: AntigravityAccount): string {
  if (account.dead) return 'dead'
  if (account.cooling_down) return 'cooling'
  return 'ok'
}

function onLogout() {
  if (confirm('Bạn có chắc chắn muốn đăng xuất khỏi TẤT CẢ tài khoản Google không? Thông tin xác thực đã lưu sẽ bị xóa.')) {
    emit('logout')
  }
}

function onRemoveAccount(account: AntigravityAccount) {
  const name = account.email || account.id
  if (confirm(`Đăng xuất tài khoản "${name}"? Các tài khoản khác vẫn hoạt động.`)) {
    emit('logoutAccount', account.id)
  }
}

function copyModel(model: string) {
  navigator.clipboard?.writeText(model).catch(() => undefined)
}
</script>

<style scoped>
.auth-card {
  margin-bottom: 24px;
}

.auth-actions {
  display: flex;
  gap: 8px;
  margin-top: 14px;
}

.auth-status {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 13px;
  background: #f5f5f5;
  color: #444;
  margin-top: 14px;
}

.auth-status .status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #9ca3af;
}

.auth-status.ok {
  background: #f0fdf4;
  color: #166534;
}

.auth-status.ok .status-dot {
  background: #16a34a;
}

.auth-status.pending {
  background: #eff6ff;
  color: #1d4ed8;
}

.auth-status.pending .status-dot {
  background: #2563eb;
}

.auth-status.error {
  background: #fef2f2;
  color: #991b1b;
}

.auth-status.error .status-dot {
  background: #dc2626;
}

.auth-accounts {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid #eee;
}

.accounts-label {
  display: block;
  font-size: 12px;
  color: #888;
  margin-bottom: 8px;
}

.account-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.account-table th {
  text-align: left;
  font-weight: 600;
  color: #666;
  padding: 6px 10px;
  border-bottom: 1px solid #eee;
}

.account-table td {
  padding: 8px 10px;
  border-bottom: 1px solid #f5f5f5;
  vertical-align: middle;
}

.account-project {
  font-family: 'Monaco', 'Menlo', monospace;
  font-size: 12px;
  color: #555;
}

.account-state {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
}

.account-state.ok {
  background: #f0fdf4;
  color: #166534;
}

.account-state.cooling {
  background: #fff7ed;
  color: #9a3412;
}

.account-state.dead {
  background: #fef2f2;
  color: #991b1b;
}

.account-actions {
  display: flex;
  gap: 6px;
  white-space: nowrap;
}

.btn-danger {
  color: #dc2626;
  border-color: #fecaca;
}

.btn-danger:hover {
  background: #fef2f2;
}

.accounts-hint {
  margin-top: 10px;
  font-size: 12px;
  color: #9a3412;
}

.auth-fallback {
  margin-top: 12px;
  font-size: 13px;
  color: #666;
}

.auth-fallback a {
  color: #2563eb;
}

.auth-models {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid #eee;
}

.model-group + .model-group {
  margin-top: 12px;
}

.model-group label {
  display: block;
  font-size: 12px;
  color: #888;
  margin-bottom: 6px;
}

.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.model-chip {
  padding: 4px 10px;
  font-size: 12px;
  border: 1px solid #e5e5e5;
  background: #fafafa;
  border-radius: 999px;
  cursor: pointer;
  color: #333;
}

.model-chip:hover {
  border-color: #c7c7c7;
  background: #f1f1f1;
}

.auth-waiting {
  margin-top: 12px;
}
</style>
