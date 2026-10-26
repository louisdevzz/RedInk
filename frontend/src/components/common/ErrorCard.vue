<template>
  <section v-if="normalized" class="error-card" role="alert" aria-live="polite">
    <div class="error-main">
      <div class="error-icon">!</div>
      <div class="error-content">
        <div class="error-title-row">
          <h3>{{ normalized.title }}</h3>
          <span v-if="normalized.code" class="error-code">{{ normalized.code }}</span>
        </div>
        <p class="error-detail">{{ normalized.detail }}</p>
        <p v-if="normalized.suggestion" class="error-suggestion">{{ normalized.suggestion }}</p>
      </div>
      <button v-if="dismissible" class="error-close" type="button" @click="$emit('dismiss')" aria-label="Đóng thông báo lỗi">
        ×
      </button>
    </div>

    <details class="error-details">
      <summary>Thông tin chẩn đoán</summary>
      <pre>{{ diagnostics }}</pre>
      <button class="copy-btn" type="button" @click="copyDiagnostics">
        {{ copied ? 'Đã sao chép' : 'Sao chép thông tin chẩn đoán' }}
      </button>
    </details>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  diagnosticsText,
  normalizeApiError,
  type AppError,
  type ErrorLike
} from '../../utils/errors'

const props = defineProps<{
  error: ErrorLike | null | undefined
  title?: string
  dismissible?: boolean
}>()

defineEmits<{
  (e: 'dismiss'): void
}>()

const copied = ref(false)

const normalized = computed<AppError | null>(() => {
  if (!props.error) return null
  return normalizeApiError(props.error, props.title || 'Thao tác thất bại')
})

const diagnostics = computed(() => normalized.value ? diagnosticsText(normalized.value) : '')

async function copyDiagnostics() {
  if (!normalized.value) return
  try {
    await navigator.clipboard.writeText(diagnostics.value)
    copied.value = true
    window.setTimeout(() => {
      copied.value = false
    }, 1600)
  } catch (error) {
    console.error('复制诊断信息失败:', error)
  }
}
</script>

<style scoped>
.error-card {
  border: 1px solid #fecaca;
  background: #fff7f7;
  border-radius: 8px;
  padding: 14px;
  color: #7f1d1d;
}

.error-main {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.error-icon {
  flex: 0 0 auto;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #dc2626;
  color: #fff;
  font-weight: 700;
  font-size: 14px;
  line-height: 1;
}

.error-content {
  flex: 1;
  min-width: 0;
}

.error-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.error-title-row h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: #7f1d1d;
}

.error-code {
  font-family: Monaco, Menlo, monospace;
  font-size: 11px;
  color: #991b1b;
  background: #fee2e2;
  padding: 2px 6px;
  border-radius: 4px;
}

.error-detail,
.error-suggestion {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.55;
}

.error-suggestion {
  color: #991b1b;
}

.error-close {
  border: none;
  background: transparent;
  color: #991b1b;
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
  padding: 0 2px;
}

.error-details {
  margin-top: 10px;
  padding-left: 34px;
}

.error-details summary {
  cursor: pointer;
  font-size: 12px;
  color: #991b1b;
  user-select: none;
}

.error-details pre {
  white-space: pre-wrap;
  word-break: break-word;
  margin: 8px 0;
  padding: 10px;
  border-radius: 6px;
  background: #fff;
  border: 1px solid #fecaca;
  color: #3f1d1d;
  font-size: 11px;
  line-height: 1.45;
  max-height: 220px;
  overflow: auto;
}

.copy-btn {
  border: 1px solid #fecaca;
  background: #fff;
  color: #991b1b;
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 12px;
  cursor: pointer;
}
</style>