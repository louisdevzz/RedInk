<template>
  <div class="container" style="max-width: 1200px;">

    <!-- Header Area -->
    <div class="page-header">
      <div>
        <h1 class="page-title">Tác phẩm của tôi</h1>
      </div>
      <div style="display: flex; gap: 10px;">
        <button
          class="btn"
          @click="handleScanAll"
          :disabled="isScanning"
          style="border: 1px solid var(--border-color);"
        >
          <svg v-if="!isScanning" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right: 6px;"><path d="M23 4v6h-6"></path><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg>
          <div v-else class="spinner-small" style="margin-right: 6px;"></div>
          {{ isScanning ? 'Đang đồng bộ...' : 'Đồng bộ lịch sử' }}
        </button>
        <button class="btn btn-primary" @click="router.push('/')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right: 6px;"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
          Tạo bài viết mới
        </button>
      </div>
    </div>

    <ErrorCard
      v-if="error"
      :error="error"
      dismissible
      style="margin-bottom: 16px;"
      @dismiss="error = null"
    />

    <div v-else-if="successMessage" class="success-card" role="status" aria-live="polite">
      <span>{{ successMessage }}</span>
      <button type="button" @click="successMessage = ''" aria-label="Đóng thông báo">×</button>
    </div>

    <!-- Stats Overview -->
    <StatsOverview v-if="stats" :stats="stats" />

    <!-- Toolbar: Tabs & Search -->
    <div class="toolbar-wrapper">
      <div class="tabs-container" style="margin-bottom: 0; border-bottom: none;">
        <div
          class="tab-item"
          :class="{ active: currentTab === 'all' }"
          @click="switchTab('all')"
        >
          Tất cả
        </div>
        <div
          class="tab-item"
          :class="{ active: currentTab === 'completed' }"
          @click="switchTab('completed')"
        >
          Đã hoàn thành
        </div>
        <div
          class="tab-item"
          :class="{ active: currentTab === 'draft' }"
          @click="switchTab('draft')"
        >
          Bản nháp
        </div>
      </div>

      <div class="search-mini">
        <svg class="icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
        <input
          v-model="searchKeyword"
          type="text"
          placeholder="Tìm kiếm tiêu đề..."
          @keyup.enter="handleSearch"
        />
      </div>
    </div>

    <!-- Content Area -->
    <div v-if="loading" class="loading-state">
      <div class="spinner"></div>
    </div>

    <div v-else-if="records.length === 0" class="empty-state-large">
      <div class="empty-img">
        <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg>
      </div>
      <h3>Chưa có bản ghi liên quan</h3>
      <p class="empty-tips">Hãy đi tạo một tác phẩm mới nhé</p>
    </div>

    <div v-else class="gallery-grid">
      <GalleryCard
        v-for="record in records"
        :key="record.id"
        :record="record"
        @preview="viewImages"
        @edit="loadRecord"
        @delete="confirmDelete"
      />
    </div>

    <!-- Pagination -->
    <div v-if="totalPages > 1" class="pagination-wrapper">
       <button class="page-btn" :disabled="currentPage === 1" @click="changePage(currentPage - 1)">Previous</button>
       <span class="page-indicator">{{ currentPage }} / {{ totalPages }}</span>
       <button class="page-btn" :disabled="currentPage === totalPages" @click="changePage(currentPage + 1)">Next</button>
    </div>

    <!-- Image Viewer Modal -->
    <ImageGalleryModal
      v-if="viewingRecord"
      :visible="!!viewingRecord"
      :record="viewingRecord"
      :regeneratingImages="regeneratingImages"
      @close="closeGallery"
      @showOutline="showOutlineModal = true"
      @regenerate="regenerateHistoryImage"
      @downloadAll="downloadAllImages"
      @download="downloadImage"
    />

    <!-- 大纲查看模态框 -->
    <OutlineModal
      v-if="showOutlineModal && viewingRecord"
      :visible="showOutlineModal"
      :pages="viewingRecord.outline.pages"
      @close="showOutlineModal = false"
    />

  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import {
  getHistoryList,
  getHistoryStats,
  searchHistory,
  deleteHistory,
  getHistory,
  type HistoryRecord,
  regenerateImage as apiRegenerateImage,
  updateHistory,
  scanAllTasks
} from '../api'
import { useGeneratorStore } from '../stores/generator'

// 引入组件
import StatsOverview from '../components/history/StatsOverview.vue'
import GalleryCard from '../components/history/GalleryCard.vue'
import ImageGalleryModal from '../components/history/ImageGalleryModal.vue'
import OutlineModal from '../components/history/OutlineModal.vue'
import ErrorCard from '../components/common/ErrorCard.vue'
import { normalizeApiError, type AppError } from '../utils/errors'

const router = useRouter()
const route = useRoute()
const store = useGeneratorStore()

// 数据状态
const records = ref<HistoryRecord[]>([])
const loading = ref(false)
const stats = ref<any>(null)
const currentTab = ref('all')
const searchKeyword = ref('')
const currentPage = ref(1)
const totalPages = ref(1)

// 查看器状态
const viewingRecord = ref<any>(null)
const regeneratingImages = ref<Set<number>>(new Set())
const showOutlineModal = ref(false)
const isScanning = ref(false)
const error = ref<AppError | null>(null)
const successMessage = ref('')

/**
 * 加载历史记录列表
 */
async function loadData() {
  loading.value = true
  error.value = null
  try {
    let statusFilter = currentTab.value === 'all' ? undefined : currentTab.value
    const res = await getHistoryList(currentPage.value, 12, statusFilter)
    if (res.success) {
      records.value = res.records
      totalPages.value = res.total_pages
    } else {
      error.value = normalizeApiError(res.error || res.error_message || 'Lấy danh sách lịch sử thất bại', 'Lấy danh sách lịch sử thất bại')
    }
  } catch(e) {
    error.value = normalizeApiError(e, 'Lấy danh sách lịch sử thất bại')
  } finally {
    loading.value = false
  }
}

/**
 * 加载统计数据
 */
async function loadStats() {
  try {
    const res = await getHistoryStats()
    if (res.success) {
      stats.value = res
    } else {
      error.value = normalizeApiError(res.error || res.error_message || 'Lấy thông tin thống kê thất bại', 'Lấy thông tin thống kê thất bại')
    }
  } catch(e) {
    error.value = normalizeApiError(e, 'Lấy thông tin thống kê thất bại')
  }
}

/**
 * 切换标签页
 */
function switchTab(tab: string) {
  currentTab.value = tab
  currentPage.value = 1
  loadData()
}

/**
 * 搜索历史记录
 */
async function handleSearch() {
  if (!searchKeyword.value.trim()) {
    loadData()
    return
  }
  loading.value = true
  error.value = null
  try {
    const res = await searchHistory(searchKeyword.value)
    if (res.success) {
      records.value = res.records
      totalPages.value = 1
    } else {
      error.value = normalizeApiError(res.error || res.error_message || 'Tìm kiếm lịch sử thất bại', 'Tìm kiếm lịch sử thất bại')
    }
  } catch(e) {
    error.value = normalizeApiError(e, 'Tìm kiếm lịch sử thất bại')
  } finally {
    loading.value = false
  }
}

/**
 * 加载记录并跳转到编辑页
 */
async function loadRecord(id: string) {
  const res = await getHistory(id)
  if (res.success && res.record) {
    store.setTopic(res.record.title)
    store.setOutline(res.record.outline.raw, res.record.outline.pages)
    store.setRecordId(res.record.id)
    const generated = res.record.images.generated || []
    if (generated.some(Boolean)) {
      store.taskId = res.record.images.task_id
      store.images = res.record.outline.pages.map((page, idx) => {
        const filename = generated[page.index] || generated[idx] || ''
        return {
          index: page.index,
          url: filename ? `/api/images/${res.record!.images.task_id}/${filename}` : '',
          status: filename ? 'done' : 'error',
          retryable: !filename
        }
      })
    }
    router.push('/outline')
  } else {
    error.value = normalizeApiError(res.error || res.error_message || 'Mở bản ghi lịch sử thất bại', 'Mở bản ghi lịch sử thất bại')
  }
}

/**
 * 查看图片
 */
async function viewImages(id: string) {
  const res = await getHistory(id)
  if (res.success) {
    viewingRecord.value = res.record
  } else {
    error.value = normalizeApiError(res.error || res.error_message || 'Xem hình ảnh thất bại', 'Xem hình ảnh thất bại')
  }
}

/**
 * 关闭图片查看器
 */
function closeGallery() {
  viewingRecord.value = null
  showOutlineModal.value = false
}

/**
 * 确认删除
 */
async function confirmDelete(record: any) {
  if(confirm('Bạn có chắc chắn muốn xóa không?')) {
    const result = await deleteHistory(record.id)
    if (result.success) {
      loadData()
      loadStats()
    } else {
      error.value = normalizeApiError(result.error || result.error_message || 'Xóa bản ghi lịch sử thất bại', 'Xóa bản ghi lịch sử thất bại')
    }
  }
}

/**
 * 切换页码
 */
function changePage(p: number) {
  currentPage.value = p
  loadData()
}

/**
 * 重新生成历史记录中的图片
 */
async function regenerateHistoryImage(index: number) {
  if (!viewingRecord.value || !viewingRecord.value.images.task_id) {
    error.value = normalizeApiError('Thiếu thông tin tác vụ, không thể tạo lại hình ảnh.', 'Không thể tạo lại')
    return
  }

  const page = viewingRecord.value.outline.pages.find((item: any) => item.index === index)
  if (!page) return

  regeneratingImages.value.add(index)

  try {
    const context = {
      fullOutline: viewingRecord.value.outline.raw || '',
      userTopic: viewingRecord.value.title || '',
      recordId: viewingRecord.value.id
    }

    const result = await apiRegenerateImage(
      viewingRecord.value.images.task_id,
      page,
      true,
      context
    )

    if (result.success && result.image_url) {
      const filename = result.image_url.split('/').pop()
      while (viewingRecord.value.images.generated.length <= index) {
        viewingRecord.value.images.generated.push('')
      }
      viewingRecord.value.images.generated[index] = filename

      // 刷新图片
      const timestamp = Date.now()
      const imgElements = document.querySelectorAll(`img[src*="${viewingRecord.value.images.task_id}/${filename}"]`)
      imgElements.forEach(img => {
        const baseUrl = (img as HTMLImageElement).src.split('?')[0]
        ;(img as HTMLImageElement).src = `${baseUrl}?t=${timestamp}`
      })

      await updateHistory(viewingRecord.value.id, {
        images: {
          task_id: viewingRecord.value.images.task_id,
          generated: viewingRecord.value.images.generated
        }
      })

      regeneratingImages.value.delete(index)
    } else {
      regeneratingImages.value.delete(index)
      error.value = normalizeApiError(result.error || result.error_message || 'Tạo lại thất bại', 'Tạo lại thất bại')
    }
  } catch (e) {
    regeneratingImages.value.delete(index)
    error.value = normalizeApiError(e, 'Tạo lại thất bại')
  }
}

/**
 * 下载单张图片
 */
function downloadImage(filename: string, index: number) {
  if (!viewingRecord.value) return
  const link = document.createElement('a')
  link.href = `/api/images/${viewingRecord.value.images.task_id}/${filename}?thumbnail=false`
  link.download = `page_${index + 1}.png`
  link.click()
}

/**
 * 打包下载所有图片
 */
function downloadAllImages() {
  if (!viewingRecord.value) return
  const link = document.createElement('a')
  link.href = `/api/history/${viewingRecord.value.id}/download`
  link.click()
}

/**
 * 扫描所有任务并同步
 */
async function handleScanAll() {
  isScanning.value = true
  try {
    const result = await scanAllTasks()
    if (result.success) {
      let message = `Quét hoàn tất!\n`
      message += `- Tổng số tác vụ: ${result.total_tasks || 0}\n`
      message += `- Đồng bộ thành công: ${result.synced || 0}\n`
      message += `- Đồng bộ thất bại: ${result.failed || 0}\n`

      if (result.orphan_tasks && result.orphan_tasks.length > 0) {
        message += `- Tác vụ mồ côi (không có bản ghi): ${result.orphan_tasks.length}\n`
      }

      successMessage.value = message
      await loadData()
      await loadStats()
    } else {
      error.value = normalizeApiError(result.error || result.error_message || 'Quét thất bại', 'Quét thất bại')
    }
  } catch (e) {
    console.error('扫描失败:', e)
    error.value = normalizeApiError(e, 'Quét thất bại')
  } finally {
    isScanning.value = false
  }
}

onMounted(async () => {
  await loadData()
  await loadStats()

  // 检查路由参数，如果有 ID 则自动打开图片查看器
  if (route.params.id) {
    await viewImages(route.params.id as string)
  }

  // 自动执行一次扫描（静默，不显示结果）
  try {
    const result = await scanAllTasks()
    if (result.success && (result.synced || 0) > 0) {
      await loadData()
      await loadStats()
    }
  } catch (e) {
    console.error('自动扫描失败:', e)
  }
})
</script>

<style scoped>
/* Small Spinner */
.spinner-small {
  width: 16px;
  height: 16px;
  border: 2px solid var(--primary);
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  display: inline-block;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* Toolbar */
.toolbar-wrapper {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
  border-bottom: 1px solid var(--border-color);
  padding-bottom: 0;
}

.search-mini {
  position: relative;
  width: 240px;
  margin-bottom: 10px;
}

.search-mini input {
  width: 100%;
  padding: 8px 12px 8px 36px;
  border-radius: 100px;
  border: 1px solid var(--border-color);
  font-size: 14px;
  background: white;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.search-mini input:focus {
  border-color: var(--primary);
  outline: none;
  box-shadow: 0 0 0 3px var(--primary-light);
}

.search-mini .icon {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: #ccc;
}

.success-card {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 16px;
  padding: 12px 14px;
  border: 1px solid #bbf7d0;
  background: #f0fdf4;
  color: #166534;
  border-radius: 8px;
  font-size: 14px;
  white-space: pre-line;
}

.success-card button {
  border: none;
  background: transparent;
  color: #166534;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
}

/* Gallery Grid */
.gallery-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 24px;
  margin-bottom: 40px;
}

/* Pagination */
.pagination-wrapper {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 16px;
  margin-top: 40px;
}

.page-btn {
  padding: 8px 16px;
  border: 1px solid var(--border-color);
  background: white;
  border-radius: 6px;
  cursor: pointer;
}

.page-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Empty State */
.empty-state-large {
  text-align: center;
  padding: 80px 0;
  color: var(--text-sub);
}

.empty-img {
  font-size: 64px;
  opacity: 0.5;
}

.empty-state-large .empty-tips {
  margin-top: 10px;
  color: var(--text-placeholder);
}
</style>
