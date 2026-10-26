import type { ComputedRef } from 'vue'
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useGeneratorStore } from '../stores/generator'
import { generateImagesPost } from '../api'
import { formatErrorMessage, normalizeApiError, type AppError } from '../utils/errors'
import { useGenerationRestore } from './useGenerationRestore'

export function useGenerationRunner(
  hasFailedImages: ComputedRef<boolean>,
  setError: (error: AppError | null) => void
) {
  const router = useRouter()
  const store = useGeneratorStore()
  const { ensureRecord, restoreFromHistory } = useGenerationRestore()
  const redirectTimer = ref<number | null>(null)
  let isUnmounted = false

  async function startGenerationFlow() {
    if (store.outline.pages.length === 0) {
      router.push('/')
      return
    }

    if (await restoreFromHistory()) return

    await ensureRecord()

    store.startGeneration()

    generateImagesPost(
      store.outline.pages,
      null,
      store.outline.raw,
      (event) => {
        console.log('Progress:', event)
      },
      (event) => {
        console.log('Complete:', event)
        if (event.image_url) {
          store.updateProgress(event.index, 'done', event.image_url)
        }
      },
      (event) => {
        console.error('Error:', event)
        store.updateProgress(
          event.index,
          'error',
          undefined,
          formatErrorMessage(event.error || event.message || 'Tạo hình ảnh thất bại', 'Tạo hình ảnh thất bại')
        )
      },
      (event) => {
        console.log('Finish:', event)
        store.finishGeneration(event.task_id)

        if (!hasFailedImages.value) {
          redirectTimer.value = window.setTimeout(() => {
            if (!isUnmounted) {
              router.push('/result')
            }
          }, 1000)
        }
      },
      (err) => {
        console.error('Stream Error:', err)
        setError(normalizeApiError(err, 'Tạo hình ảnh thất bại'))
      },
      store.userImages.length > 0 ? store.userImages : undefined,
      store.topic,
      store.recordId
    )
  }

  function cleanupGenerationRunner() {
    isUnmounted = true
    if (redirectTimer.value !== null) {
      clearTimeout(redirectTimer.value)
      redirectTimer.value = null
    }
  }

  return {
    cleanupGenerationRunner,
    startGenerationFlow
  }
}
