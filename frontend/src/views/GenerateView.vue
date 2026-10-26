<template>
  <div class="container">
    <div class="page-header">
      <div>
        <h1 class="page-title">Kết quả tạo</h1>
        <p class="page-subtitle">
          <span v-if="isGenerating">Đang tạo trang {{ store.progress.current + 1 }} / {{ store.progress.total }}</span>
          <span v-else-if="hasFailedImages">{{ failedCount }} hình ảnh tạo thất bại, có thể nhấn để thử lại</span>
          <span v-else>Đã tạo xong toàn bộ {{ store.progress.total }} hình ảnh</span>
        </p>
      </div>
      <div style="display: flex; gap: 10px;">
        <button
          v-if="hasFailedImages && !isGenerating"
          class="btn btn-primary"
          @click="retryAllFailed"
          :disabled="isRetrying"
        >
          {{ isRetrying ? 'Đang hoàn thiện...' : 'Hoàn thiện ảnh lỗi' }}
        </button>
        <button class="btn" @click="router.push('/outline')" style="border:1px solid var(--border-color)">
          Về dàn ý
        </button>
      </div>
    </div>

    <div class="card">
      <div style="margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center;">
        <span style="font-weight: 600;">Tiến độ tạo</span>
        <span style="color: var(--primary); font-weight: 600;">{{ Math.round(progressPercent) }}%</span>
      </div>
      <div class="progress-container">
        <div class="progress-bar" :style="{ width: progressPercent + '%' }" />
      </div>

      <ErrorCard
        v-if="error"
        :error="error"
        dismissible
        style="margin-top: 18px;"
        @dismiss="error = null"
      />

      <div class="grid-cols-4" style="margin-top: 40px;">
        <div v-for="image in store.images" :key="image.index" class="image-card">
          <!-- 图片展示区域 -->
          <div v-if="image.url && image.status === 'done'" class="image-preview">
            <img :src="image.url" :alt="`Trang ${image.index + 1}`" />
            <!-- 重新生成按钮（悬停显示） -->
            <div class="image-overlay">
              <button
                class="overlay-btn"
                @click="regenerateImage(image.index)"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M23 4v6h-6"></path>
                  <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
                </svg>
                Tạo lại
              </button>
            </div>
          </div>

          <!-- 生成中/重试中状态 -->
          <div v-else-if="image.status === 'generating' || image.status === 'retrying'" class="image-placeholder">
            <div class="spinner"></div>
            <div class="status-text">{{ image.status === 'retrying' ? 'Đang thử lại...' : 'Đang tạo...' }}</div>
          </div>

          <!-- 失败状态 -->
          <div v-else-if="image.status === 'error'" class="image-placeholder error-placeholder">
            <div class="error-icon">!</div>
            <div class="status-text">Tạo thất bại</div>
            <div v-if="image.error" class="image-error-text">{{ image.error }}</div>
            <button
              class="retry-btn"
              @click="retrySingleImage(image.index)"
              :disabled="isRetrying"
            >
              Nhấn để thử lại
            </button>
          </div>

          <!-- 等待中状态 -->
          <div v-else class="image-placeholder">
            <div class="status-text">Đang chờ</div>
          </div>

          <!-- 底部信息栏 -->
          <div class="image-footer">
            <span class="page-label">Page {{ image.index + 1 }}</span>
            <span class="status-badge" :class="image.status">
              {{ getStatusText(image.status) }}
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useGeneratorStore } from '../stores/generator'
import ErrorCard from '../components/common/ErrorCard.vue'
import { useGenerationRunner } from '../composables/useGenerationRunner'
import { useImageRetry } from '../composables/useImageRetry'
import type { AppError } from '../utils/errors'

const router = useRouter()
const store = useGeneratorStore()

const error = ref<AppError | null>(null)

const isGenerating = computed(() => store.progress.status === 'generating')

const progressPercent = computed(() => {
  if (store.progress.total === 0) return 0
  return (store.progress.current / store.progress.total) * 100
})

const hasFailedImages = computed(() => store.images.some(img => img.status === 'error'))

const failedCount = computed(() => store.images.filter(img => img.status === 'error').length)

const getStatusText = (status: string) => {
  const texts: Record<string, string> = {
    generating: 'Đang tạo',
    done: 'Hoàn thành',
    error: 'Thất bại',
    retrying: 'Đang thử lại'
  }
  return texts[status] || 'Đang chờ'
}

function setError(nextError: AppError | null) {
  error.value = nextError
}

const {
  isRetrying,
  regenerateImage,
  retryAllFailed,
  retrySingleImage
} = useImageRetry(setError)

const {
  cleanupGenerationRunner,
  startGenerationFlow
} = useGenerationRunner(hasFailedImages, setError)

onMounted(startGenerationFlow)
onUnmounted(cleanupGenerationRunner)
</script>

<style scoped>
.image-preview {
  aspect-ratio: 3/4;
  overflow: hidden;
  position: relative;
  flex: 1; /* 填充卡片剩余空间 */
}

.image-preview img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.image-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity 0.2s;
}

.image-preview:hover .image-overlay {
  opacity: 1;
}

.overlay-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  background: white;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  color: #333;
  transition: all 0.2s;
}

.overlay-btn:hover {
  background: var(--primary);
  color: white;
}

.overlay-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.image-placeholder {
  aspect-ratio: 3/4;
  background: #f9f9f9;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  flex: 1; /* 填充卡片剩余空间 */
  min-height: 240px; /* 确保有最小高度 */
}

.error-placeholder {
  background: #fff5f5;
}

.error-icon {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: #ff4d4f;
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  font-weight: bold;
}

.status-text {
  font-size: 13px;
  color: var(--text-sub);
}

.image-error-text {
  max-width: 85%;
  color: #991b1b;
  font-size: 12px;
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.retry-btn {
  margin-top: 8px;
  padding: 6px 16px;
  background: var(--primary);
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  transition: all 0.2s;
}

.retry-btn:hover {
  opacity: 0.9;
  transform: translateY(-1px);
}

.retry-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
}

.image-footer {
  padding: 12px;
  border-top: 1px solid #f0f0f0;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.page-label {
  font-size: 12px;
  color: var(--text-sub);
}

.status-badge {
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 4px;
}

.status-badge.done {
  background: #E6F7ED;
  color: #52C41A;
}

.status-badge.generating,
.status-badge.retrying {
  background: #E6F4FF;
  color: #1890FF;
}

.status-badge.error {
  background: #FFF1F0;
  color: #FF4D4F;
}

.spinner {
  width: 24px;
  height: 24px;
  border: 2px solid var(--primary);
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
