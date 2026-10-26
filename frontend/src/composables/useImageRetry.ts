import { ref } from 'vue'
import { useGeneratorStore } from '../stores/generator'
import {
  regenerateImage as apiRegenerateImage,
  retryFailedImages as apiRetryFailed
} from '../api'
import {
  formatErrorMessage,
  normalizeApiError,
  type AppError
} from '../utils/errors'

export function useImageRetry(setError: (error: AppError | null) => void) {
  const store = useGeneratorStore()
  const isRetrying = ref(false)
  const regeneratingIndices = ref(new Set<number>())

  function finishIfAllImagesDone() {
    if (store.taskId && store.images.length > 0 && store.images.every(img => img.status === 'done')) {
      store.finishGeneration(store.taskId)
    }
  }

  function retrySingleImage(index: number) {
    if (!store.taskId || regeneratingIndices.value.has(index)) return

    const page = store.outline.pages.find(p => p.index === index)
    if (!page) return

    regeneratingIndices.value.add(index)
    store.setImageRetrying(index)

    const context = {
      fullOutline: store.outline.raw || '',
      userTopic: store.topic || '',
      recordId: store.recordId
    }

    apiRegenerateImage(store.taskId, page, true, context)
      .then(result => {
        if (result.success && result.image_url) {
          store.updateImage(index, result.image_url)
          finishIfAllImagesDone()
        } else {
          store.updateProgress(
            index,
            'error',
            undefined,
            formatErrorMessage(result.error || result.error_message || 'Vẽ lại thất bại', 'Vẽ lại thất bại')
          )
        }
      })
      .catch(e => {
        store.updateProgress(index, 'error', undefined, formatErrorMessage(e, 'Vẽ lại thất bại'))
      })
      .finally(() => {
        regeneratingIndices.value.delete(index)
      })
  }

  function regenerateImage(index: number) {
    retrySingleImage(index)
  }

  async function retryAllFailed() {
    if (!store.taskId) return

    const failedPages = store.getFailedPages()
    if (failedPages.length === 0) return

    isRetrying.value = true
    failedPages.forEach(page => {
      store.setImageRetrying(page.index)
    })

    try {
      await apiRetryFailed(
        store.taskId,
        failedPages,
        () => {},
        (event) => {
          if (event.image_url) {
            store.updateImage(event.index, event.image_url)
          }
        },
        (event) => {
          store.updateProgress(
            event.index,
            'error',
            undefined,
            formatErrorMessage(event.error || event.message || 'Tạo bổ sung ảnh thất bại', 'Tạo bổ sung ảnh thất bại')
          )
        },
        (event) => {
          isRetrying.value = false
          if (event.failed === 0) {
            finishIfAllImagesDone()
          } else {
            store.progress.status = 'error'
          }
        },
        (err) => {
          console.error('重试失败:', err)
          isRetrying.value = false
          setError(normalizeApiError(err, 'Tạo bổ sung ảnh thất bại'))
        },
        store.recordId
      )
    } catch (e) {
      isRetrying.value = false
      setError(normalizeApiError(e, 'Tạo bổ sung ảnh thất bại'))
    }
  }

  return {
    isRetrying,
    regenerateImage,
    retryAllFailed,
    retrySingleImage
  }
}
