<template>
  <!-- 图片服务商编辑/添加弹窗 -->
  <div v-if="visible" class="modal-overlay" @click="$emit('close')">
    <div class="modal-content" @click.stop>
      <div class="modal-header">
        <h3>{{ isEditing ? 'Chỉnh sửa nhà cung cấp' : 'Thêm nhà cung cấp' }}</h3>
        <button class="close-btn" @click="$emit('close')">×</button>
      </div>

      <div class="modal-body">
        <!-- 服务商名称（仅添加时显示） -->
        <div class="form-group" v-if="!isEditing">
          <label>Tên nhà cung cấp</label>
          <input
            type="text"
            class="form-input"
            :value="formData.name"
            @input="updateField('name', ($event.target as HTMLInputElement).value)"
            placeholder="VD: google_genai"
          />
          <span class="form-hint">Mã định danh duy nhất, dùng để phân biệt các nhà cung cấp khác nhau</span>
        </div>

        <!-- 类型选择 -->
        <div class="form-group">
          <label>Loại</label>
          <select
            class="form-select"
            :value="formData.type"
            @change="updateField('type', ($event.target as HTMLSelectElement).value)"
          >
            <option v-for="opt in typeOptions" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </option>
          </select>
        </div>

        <!-- API Key（OAuth 类型无需填写） -->
        <div class="form-group" v-if="!isOauth">
          <label>API Key</label>
          <input
            type="text"
            class="form-input"
            :value="formData.api_key"
            @input="updateField('api_key', ($event.target as HTMLInputElement).value)"
            :placeholder="isEditing && formData._has_api_key ? formData.api_key_masked : 'Nhập API Key'"
          />
          <span class="form-hint" v-if="isEditing && formData._has_api_key">
            Đã cấu hình API Key, để trống nghĩa là giữ nguyên
          </span>
        </div>

        <!-- Google 账号登录提示 -->
        <div class="form-group" v-else>
          <label>Phương thức xác thực</label>
          <span class="oauth-badge" :class="{ offline: !oauthConnected }">
            {{ oauthConnected ? `Đã đăng nhập Google 账号登录: ${oauthEmail || 'Chưa rõ'}` : 'Chưa đăng nhập Google 账号登录' }}
          </span>
          <span class="form-hint">
            Loại này dùng hạn ngạch Google 账号登录 (Antigravity) để tạo hình ảnh, không cần API Key.
            Vui lòng hoàn tất trong thẻ "Google 账号登录" trên trang cài đặt hệ thống.
          </span>
        </div>

        <!-- Base URL -->
        <div class="form-group" v-if="showBaseUrl">
          <label>Base URL</label>
          <input
            type="text"
            class="form-input"
            :value="formData.base_url"
            @input="updateField('base_url', ($event.target as HTMLInputElement).value)"
            :placeholder="baseUrlPlaceholder"
          />
          <span class="form-hint" v-if="previewUrl">
            Xem trước: {{ previewUrl }}
          </span>
        </div>

        <!-- 模型 -->
        <div class="form-group">
          <label>Mô hình</label>
          <input
            type="text"
            class="form-input"
            :value="formData.model"
            @input="updateField('model', ($event.target as HTMLInputElement).value)"
            :placeholder="modelPlaceholder"
            :list="modelListId"
          />
          <datalist v-if="hasSuggestions" :id="modelListId">
            <option v-for="model in modelSuggestions" :key="model" :value="model"></option>
          </datalist>
          <span class="form-hint" v-if="hasSuggestions">
            Điền auto để tự động dùng mô hình hình ảnh được cung cấp bởi tài khoản này
          </span>
        </div>

        <!-- 端点路径（仅 OpenAI 兼容接口） -->
        <div class="form-group" v-if="showEndpointType">
          <label>Đường dẫn API endpoint</label>
          <input
            type="text"
            class="form-input"
            :value="formData.endpoint_type"
            @input="updateField('endpoint_type', ($event.target as HTMLInputElement).value)"
            placeholder="VD: /v1/images/generations hoặc /v1/chat/completions"
          />
          <span class="form-hint">
            Endpoint phổ biến: /v1/images/generations (tạo ảnh tiêu chuẩn), /v1/chat/completions (API trả link như Jimeng)
          </span>
        </div>

        <!-- 高并发模式 -->
        <div class="form-group">
          <label class="toggle-label">
            <span>Chế độ đồng thời cao</span>
            <div
              class="toggle-switch"
              :class="{ active: formData.high_concurrency }"
              @click="updateField('high_concurrency', !formData.high_concurrency)"
            >
              <div class="toggle-slider"></div>
            </div>
          </label>
          <span class="form-hint">
            Bật tính năng này sẽ tạo ảnh song song, nhanh hơn nhưng yêu cầu chất lượng API cao hơn. Không khuyến nghị cho tài khoản dùng thử GCP 300$.
          </span>
        </div>

        <!-- 短 Prompt 模式 -->
        <div class="form-group">
          <label class="toggle-label">
            <span>Chế độ Prompt ngắn</span>
            <div
              class="toggle-switch"
              :class="{ active: formData.short_prompt }"
              @click="updateField('short_prompt', !formData.short_prompt)"
            >
              <div class="toggle-slider"></div>
            </div>
          </label>
          <span class="form-hint">
            Bật tính năng này sẽ dùng prompt rút gọn, phù hợp với API bị giới hạn ký tự (như giới hạn 1600 ký tự của Jimeng).
          </span>
        </div>
      </div>

      <div class="modal-footer">
        <button class="btn" @click="$emit('close')">Hủy</button>
        <button
          class="btn btn-secondary"
          @click="$emit('test')"
          :disabled="testing || (!formData.api_key && !isEditing && !isOauth)"
        >
          <span v-if="testing" class="spinner-small"></span>
          {{ testing ? 'Đang kiểm tra...' : 'Kiểm tra kết nối' }}
        </button>
        <button class="btn btn-primary" @click="$emit('save')">
          {{ isEditing ? 'Lưu' : 'Thêm' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { AntigravityCredentialState } from '../../api'
import type { ImageProviderForm } from '../../composables/useProviderForm'

/**
 * 图片服务商编辑/添加弹窗组件
 *
 * 功能：
 * - 添加新服务商
 * - 编辑现有服务商
 * - 测试连接
 * - 支持高并发模式和短 Prompt 模式开关
 */

// 定义类型选项
interface TypeOption {
  value: string
  label: string
}

// 定义 Props
const props = defineProps<{
  visible: boolean
  isEditing: boolean
  formData: ImageProviderForm
  testing: boolean
  typeOptions: TypeOption[]
  /** Google 账号登录状态（OAuth 类型使用） */
  oauthState?: AntigravityCredentialState | null
  /** 已登录账号可用的图片模型清单 */
  modelSuggestions?: string[]
}>()

// 定义 Emits
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'save'): void
  (e: 'test'): void
  (e: 'update:formData', data: ImageProviderForm): void
}>()

// 更新表单字段
function updateField(field: keyof ImageProviderForm, value: string | boolean) {
  emit('update:formData', {
    ...props.formData,
    [field]: value
  })
}

// 是否显示 Base URL
const showBaseUrl = computed(() => {
  return ['image_api', 'google_genai', 'google_antigravity'].includes(props.formData.type)
})

// OAuth 类型（Google 账号登录）不需要 API Key
const isOauth = computed(() => props.formData.type === 'google_antigravity')
const oauthConnected = computed(() => Boolean(props.oauthState?.connected))
const oauthEmail = computed(() => props.oauthState?.email ?? '')

const modelListId = 'antigravity-image-model-suggestions'
const hasSuggestions = computed(() => isOauth.value && (props.modelSuggestions?.length ?? 0) > 0)

const baseUrlPlaceholder = computed(() =>
  props.formData.type === 'google_antigravity'
    ? 'Có thể để trống (mặc định https://daily-cloudcode-pa.googleapis.com)'
    : 'VD: https://generativelanguage.googleapis.com'
)

// 是否显示端点类型
const showEndpointType = computed(() => {
  return props.formData.type === 'image_api'
})

// 模型占位符
const modelPlaceholder = computed(() => {
  switch (props.formData.type) {
    case 'google_genai':
      return 'VD: imagen-3.0-generate-002'
    case 'image_api':
      return 'VD: flux-pro'
    case 'google_antigravity':
      return 'auto hoặc VD: gemini-3-pro-image'
    default:
      return 'VD: gpt-4o'
  }
})

// 预览 URL
const previewUrl = computed(() => {
  if (!props.formData.base_url) return ''

  const baseUrl = props.formData.base_url.replace(/\/$/, '').replace(/\/v1$/, '')

  switch (props.formData.type) {
    case 'image_api': {
      // 使用用户自定义的端点路径
      let endpoint = props.formData.endpoint_type || '/v1/images/generations'
      if (!endpoint.startsWith('/')) {
        endpoint = '/' + endpoint
      }
      return `${baseUrl}${endpoint}`
    }
    case 'google_genai':
      return `${baseUrl}/v1beta/models/${props.formData.model || '{model}'}:generateImages`
    case 'google_antigravity':
      return `${baseUrl}/v1internal:streamGenerateContent?alt=sse`
    default:
      return ''
  }
})
</script>

<style scoped>
/* 模态框遮罩 */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 20px;
}

/* 模态框内容 */
.modal-content {
  background: white;
  border-radius: 12px;
  width: 100%;
  max-width: 500px;
  max-height: 90vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.2);
}

/* 头部 */
.modal-header {
  padding: 20px 24px;
  border-bottom: 1px solid var(--border-color, #eee);
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.modal-header h3 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}

.close-btn {
  background: none;
  border: none;
  font-size: 24px;
  cursor: pointer;
  color: #999;
  padding: 0;
  line-height: 1;
}

.close-btn:hover {
  color: #333;
}

/* 主体 */
.modal-body {
  padding: 24px;
  overflow-y: auto;
  flex: 1;
}

/* 表单组 */
.form-group {
  margin-bottom: 20px;
}

.form-group:last-child {
  margin-bottom: 0;
}

.form-group label {
  display: block;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-main, #1a1a1a);
  margin-bottom: 8px;
}

.form-input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border-color, #eee);
  border-radius: 8px;
  font-size: 14px;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.form-input:focus {
  outline: none;
  border-color: var(--primary, #ff2442);
  box-shadow: 0 0 0 3px rgba(255, 36, 66, 0.1);
}

.form-select {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border-color, #eee);
  border-radius: 8px;
  font-size: 14px;
  background: white;
  cursor: pointer;
}

.form-hint {
  display: block;
  font-size: 12px;
  color: var(--text-sub, #666);
  margin-top: 6px;
}

/* Toggle 开关样式 */
.toggle-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
}

.toggle-switch {
  width: 44px;
  height: 24px;
  background: #d1d5db;
  border-radius: 12px;
  position: relative;
  transition: background 0.2s;
  flex-shrink: 0;
}

.toggle-switch.active {
  background: var(--primary, #ff2442);
}

.toggle-slider {
  width: 20px;
  height: 20px;
  background: white;
  border-radius: 50%;
  position: absolute;
  top: 2px;
  left: 2px;
  transition: transform 0.2s;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
}

.toggle-switch.active .toggle-slider {
  transform: translateX(20px);
}

/* 底部 */
.modal-footer {
  padding: 16px 24px;
  border-top: 1px solid var(--border-color, #eee);
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

/* 按钮样式 */
.btn {
  padding: 8px 16px;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid var(--border-color, #eee);
  background: white;
  color: var(--text-main, #1a1a1a);
  transition: all 0.2s;
}

.btn:hover {
  background: #f5f5f5;
}

.btn-primary {
  background: var(--primary, #ff2442);
  border-color: var(--primary, #ff2442);
  color: white;
}

.btn-primary:hover {
  background: var(--primary-hover, #e61e3a);
}

.btn-secondary {
  background: #f0f0f0;
  border-color: #ddd;
  color: #333;
}

.btn-secondary:hover {
  background: #e5e5e5;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 加载动画 */
.spinner-small {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin-right: 6px;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* OAuth（Google 账号登录）提示 */
.oauth-badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 13px;
  background: #f0fdf4;
  color: #166534;
  margin-bottom: 6px;
}

.oauth-badge.offline {
  background: #fffbeb;
  color: #92400e;
}
</style>
