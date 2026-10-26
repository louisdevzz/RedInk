import { ref } from 'vue'
import { getConfig, updateConfig, testConnection, type Config } from '../api'
import { normalizeApiError, type AppError } from '../utils/errors'

/**
 * 服务商表单管理 Composable
 *
 * 提供服务商配置的完整管理功能：
 * - 加载/保存配置
 * - 添加/编辑/删除服务商
 * - 测试连接
 * - 激活服务商
 */

// 服务商数据类型
export interface AntigravityCredentialState {
  connected: boolean
  email?: string | null
  project_id?: string | null
  expires_at?: number | null
  expires_in_seconds?: number | null
  can_refresh?: boolean
  needs_relogin?: boolean
}

export interface Provider {
  type: string
  model: string
  base_url?: string
  api_key?: string
  api_key_masked?: string
  endpoint_type?: string
  high_concurrency?: boolean
  short_prompt?: boolean
  auth?: string
  /** OAuth 登录状态（由后端只读返回，保存时会被剥离） */
  oauth?: AntigravityCredentialState
  [key: string]: unknown
}

// 服务商配置类型
export interface ProviderConfig {
  active_provider: string
  providers: Record<string, Provider>
}

// 文本服务商表单类型
export interface TextProviderForm {
  name: string
  type: string
  api_key: string
  api_key_masked: string
  base_url: string
  model: string
  endpoint_type: string
  _has_api_key: boolean
}

// 图片服务商表单类型
export interface ImageProviderForm {
  name: string
  type: string
  api_key: string
  api_key_masked: string
  base_url: string
  model: string
  high_concurrency: boolean
  short_prompt: boolean
  endpoint_type: string
  _has_api_key: boolean
}

// OAuth 服务商类型（Google 账号登录，不需要 API Key）
export const OAUTH_PROVIDER_TYPE = 'google_antigravity'

export function isOauthProviderType(type?: string): boolean {
  return type === OAUTH_PROVIDER_TYPE
}

// 文本服务商类型选项
export const textTypeOptions = [
  { value: 'google_antigravity', label: 'Google 账号登录 (Antigravity, không cần API Key)' },
  { value: 'google_gemini', label: 'Google Gemini' },
  { value: 'openai_compatible', label: 'Giao diện tương thích OpenAI' }
]

// 图片服务商类型选项
export const imageTypeOptions = [
  { value: 'google_antigravity', label: 'Google 账号登录 (Antigravity, không cần API Key)' },
  { value: 'google_genai', label: 'Google GenAI' },
  { value: 'image_api', label: 'Giao diện tương thích OpenAI' }
]

/**
 * 服务商表单管理 Hook
 */
export function useProviderForm() {
  // 加载状态
  const loading = ref(true)
  const saving = ref(false)
  const testingText = ref(false)
  const testingImage = ref(false)
  const feedback = ref<{
    type: 'success' | 'error'
    message?: string
    error?: AppError
  } | null>(null)

  // 配置数据
  const textConfig = ref<ProviderConfig>({
    active_provider: '',
    providers: {}
  })

  const imageConfig = ref<ProviderConfig>({
    active_provider: '',
    providers: {}
  })

  // 文本服务商弹窗状态
  const showTextModal = ref(false)
  const editingTextProvider = ref<string | null>(null)
  const textForm = ref<TextProviderForm>(createEmptyTextForm())

  // 图片服务商弹窗状态
  const showImageModal = ref(false)
  const editingImageProvider = ref<string | null>(null)
  const imageForm = ref<ImageProviderForm>(createEmptyImageForm())

  /**
   * 创建空的文本服务商表单
   */
  function createEmptyTextForm(): TextProviderForm {
    return {
      name: '',
      type: OAUTH_PROVIDER_TYPE,
      api_key: '',
      api_key_masked: '',
      base_url: '',
      model: 'gemini-3.1-pro-low',
      endpoint_type: '/v1/chat/completions',
      _has_api_key: false
    }
  }

  /**
   * 创建空的图片服务商表单
   */
  function createEmptyImageForm(): ImageProviderForm {
    return {
      name: '',
      type: OAUTH_PROVIDER_TYPE,
      api_key: '',
      api_key_masked: '',
      base_url: '',
      model: 'auto',
      high_concurrency: false,
      short_prompt: false,
      endpoint_type: '/v1/images/generations',
      _has_api_key: false
    }
  }

  /**
   * 加载配置
   */
  async function loadConfig() {
    loading.value = true
    try {
      const result = await getConfig()
      if (result.success && result.config) {
        textConfig.value = {
          active_provider: result.config.text_generation.active_provider,
          providers: result.config.text_generation.providers
        }
        imageConfig.value = result.config.image_generation
      } else {
        setError(result.error || result.error_message || 'Tải cấu hình thất bại', 'Tải cấu hình thất bại')
      }
    } catch (e) {
      setError(e, 'Tải cấu hình thất bại')
    } finally {
      loading.value = false
    }
  }

  /**
   * 自动保存配置
   */
  async function autoSaveConfig() {
    try {
      const config: Partial<Config> = {
        text_generation: {
          active_provider: textConfig.value.active_provider,
          providers: textConfig.value.providers
        },
        image_generation: imageConfig.value
      }

      const result = await updateConfig(config)
      if (result.success) {
        // 重新加载配置以获取最新的脱敏 API Key
        await loadConfig()
        setSuccess(result.message || 'Đã lưu cấu hình')
      }
    } catch (e) {
      console.error('自动保存失败:', e)
      setError(e, 'Lưu cấu hình thất bại')
    }
  }

  function setSuccess(message: string) {
    feedback.value = { type: 'success', message }
  }

  function setError(error: unknown, fallbackTitle = 'Thao tác thất bại') {
    feedback.value = {
      type: 'error',
      error: normalizeApiError(error, fallbackTitle)
    }
  }

  function clearFeedback() {
    feedback.value = null
  }

  // ==================== 文本服务商操作 ====================

  /**
   * 激活文本服务商
   */
  async function activateTextProvider(name: string) {
    textConfig.value.active_provider = name
    await autoSaveConfig()
  }

  /**
   * 打开添加文本服务商弹窗
   */
  function openAddTextModal() {
    editingTextProvider.value = null
    textForm.value = createEmptyTextForm()
    showTextModal.value = true
  }

  /**
   * 打开编辑文本服务商弹窗
   */
  function openEditTextModal(name: string, provider: Provider) {
    editingTextProvider.value = name
    textForm.value = {
      name: name,
      type: provider.type || 'openai_compatible',
      api_key: '',
      api_key_masked: provider.api_key_masked || '',
      base_url: provider.base_url || '',
      model: provider.model || '',
      endpoint_type: provider.endpoint_type || '/v1/chat/completions',
      _has_api_key: !!provider.api_key_masked
    }
    showTextModal.value = true
  }

  /**
   * 关闭文本服务商弹窗
   */
  function closeTextModal() {
    showTextModal.value = false
    editingTextProvider.value = null
  }

  /**
   * 保存文本服务商
   */
  async function saveTextProvider() {
    const name = editingTextProvider.value || textForm.value.name

    if (!name) {
      setError('Vui lòng điền tên nhà cung cấp', 'Cấu hình chưa đầy đủ')
      return
    }

    if (!textForm.value.type) {
      setError('Vui lòng chọn loại nhà cung cấp', 'Cấu hình chưa đầy đủ')
      return
    }

    const oauth = isOauthProviderType(textForm.value.type)

    // OAuth 服务商走 Google 登录，不需要 API Key；其它类型新增时必须填 Key
    if (!oauth && !editingTextProvider.value && !textForm.value.api_key) {
      setError('Vui lòng điền API Key', 'Cấu hình chưa đầy đủ')
      return
    }

    const existingProvider: Partial<Provider> = textConfig.value.providers[name] || {}

    const providerData: Provider = {
      type: textForm.value.type,
      model: textForm.value.model
    }

    // OAuth 服务商标记 auth，让后端跳过 API Key 校验
    if (oauth) {
      providerData.auth = 'antigravity_oauth'
    } else {
      // 如果填写了新的 API Key，使用新的；否则保留原有的
      if (textForm.value.api_key) {
        providerData.api_key = textForm.value.api_key
      } else if (existingProvider.api_key) {
        providerData.api_key = existingProvider.api_key
      }
    }

    if (textForm.value.base_url) {
      providerData.base_url = textForm.value.base_url
    }

    // 如果是 OpenAI 兼容接口，保存 endpoint_type
    if (textForm.value.type === 'openai_compatible') {
      providerData.endpoint_type = textForm.value.endpoint_type
    }

    textConfig.value.providers[name] = providerData

    closeTextModal()
    await autoSaveConfig()
  }

  /**
   * 删除文本服务商
   */
  async function deleteTextProvider(name: string) {
    if (confirm(`Bạn có chắc chắn muốn xóa nhà cung cấp "${name}" không?`)) {
      delete textConfig.value.providers[name]
      if (textConfig.value.active_provider === name) {
        textConfig.value.active_provider = ''
      }
      await autoSaveConfig()
    }
  }

  /**
   * 测试文本服务商连接（弹窗中）
   */
  async function testTextConnection() {
    testingText.value = true
    try {
      const result = await testConnection({
        type: textForm.value.type,
        provider_name: editingTextProvider.value || undefined,
        api_key: textForm.value.api_key || undefined,
        base_url: textForm.value.base_url,
        endpoint_type: textForm.value.endpoint_type,
        model: textForm.value.model
      })
      if (result.success) {
        setSuccess(result.message || 'Kết nối thành công')
      }
    } catch (e: any) {
      setError(e, 'Kết nối thất bại')
    } finally {
      testingText.value = false
    }
  }

  /**
   * 测试列表中的文本服务商
   */
  async function testTextProviderInList(name: string, provider: Provider) {
    try {
      const result = await testConnection({
        type: provider.type,
        provider_name: name,
        api_key: undefined,
        base_url: provider.base_url,
        endpoint_type: provider.endpoint_type,
        model: provider.model
      })
      if (result.success) {
        setSuccess(`${name} Kết nối thành công`)
      }
    } catch (e: any) {
      setError(e, `${name} Kết nối thất bại`)
    }
  }

  // ==================== 图片服务商操作 ====================

  /**
   * 激活图片服务商
   */
  async function activateImageProvider(name: string) {
    imageConfig.value.active_provider = name
    await autoSaveConfig()
  }

  /**
   * 打开添加图片服务商弹窗
   */
  function openAddImageModal() {
    editingImageProvider.value = null
    imageForm.value = createEmptyImageForm()
    showImageModal.value = true
  }

  /**
   * 打开编辑图片服务商弹窗
   */
  function openEditImageModal(name: string, provider: Provider) {
    editingImageProvider.value = name
    imageForm.value = {
      name: name,
      type: provider.type || '',
      api_key: '',
      api_key_masked: provider.api_key_masked || '',
      base_url: provider.base_url || '',
      model: provider.model || '',
      high_concurrency: provider.high_concurrency || false,
      short_prompt: provider.short_prompt || false,
      endpoint_type: provider.endpoint_type || '/v1/images/generations',
      _has_api_key: !!provider.api_key_masked
    }
    showImageModal.value = true
  }

  /**
   * 关闭图片服务商弹窗
   */
  function closeImageModal() {
    showImageModal.value = false
    editingImageProvider.value = null
  }

  /**
   * 保存图片服务商
   */
  async function saveImageProvider() {
    const name = editingImageProvider.value || imageForm.value.name

    if (!name) {
      setError('Vui lòng điền tên nhà cung cấp', 'Cấu hình chưa đầy đủ')
      return
    }

    if (!imageForm.value.type) {
      setError('Vui lòng chọn loại nhà cung cấp', 'Cấu hình chưa đầy đủ')
      return
    }

    const oauth = isOauthProviderType(imageForm.value.type)

    // OAuth 服务商走 Google 登录，不需要 API Key；其它类型新增时必须填 Key
    if (!oauth && !editingImageProvider.value && !imageForm.value.api_key) {
      setError('Vui lòng điền API Key', 'Cấu hình chưa đầy đủ')
      return
    }

    const existingProvider: Partial<Provider> = imageConfig.value.providers[name] || {}

    const providerData: Provider = {
      ...existingProvider,
      type: imageForm.value.type,
      model: imageForm.value.model,
      high_concurrency: imageForm.value.high_concurrency,
      short_prompt: imageForm.value.short_prompt
    }

    // OAuth 服务商标记 auth，让后端跳过 API Key 校验
    if (oauth) {
      providerData.auth = 'antigravity_oauth'
      delete providerData.api_key
      delete providerData.oauth
    } else if (imageForm.value.api_key) {
      providerData.api_key = imageForm.value.api_key
    } else if (existingProvider.api_key) {
      providerData.api_key = existingProvider.api_key
    }

    // 如果是 OpenAI 兼容接口，保存 endpoint_type
    if (imageForm.value.type === 'image_api') {
      providerData.endpoint_type = imageForm.value.endpoint_type
    }

    if (imageForm.value.base_url) {
      providerData.base_url = imageForm.value.base_url
    }

    imageConfig.value.providers[name] = providerData

    closeImageModal()
    await autoSaveConfig()
  }

  /**
   * 删除图片服务商
   */
  async function deleteImageProvider(name: string) {
    if (confirm(`Bạn có chắc chắn muốn xóa nhà cung cấp "${name}" không?`)) {
      delete imageConfig.value.providers[name]
      if (imageConfig.value.active_provider === name) {
        imageConfig.value.active_provider = ''
      }
      await autoSaveConfig()
    }
  }

  /**
   * 测试图片服务商连接（弹窗中）
   */
  async function testImageConnection() {
    testingImage.value = true
    try {
      const result = await testConnection({
        type: imageForm.value.type,
        provider_name: editingImageProvider.value || undefined,
        api_key: imageForm.value.api_key || undefined,
        base_url: imageForm.value.base_url,
        endpoint_type: imageForm.value.endpoint_type,
        model: imageForm.value.model
      })
      if (result.success) {
        setSuccess(result.message || 'Kết nối thành công')
      }
    } catch (e: any) {
      setError(e, 'Kết nối thất bại')
    } finally {
      testingImage.value = false
    }
  }

  /**
   * 测试列表中的图片服务商
   */
  async function testImageProviderInList(name: string, provider: Provider) {
    try {
      const result = await testConnection({
        type: provider.type,
        provider_name: name,
        api_key: undefined,
        base_url: provider.base_url,
        endpoint_type: provider.endpoint_type,
        model: provider.model
      })
      if (result.success) {
        setSuccess(`${name} Kết nối thành công`)
      }
    } catch (e: any) {
      setError(e, `${name} Kết nối thất bại`)
    }
  }

  /**
   * 更新文本表单数据
   */
  function updateTextForm(data: TextProviderForm) {
    textForm.value = data
  }

  /**
   * 更新图片表单数据
   */
  function updateImageForm(data: ImageProviderForm) {
    imageForm.value = data
  }

  return {
    // 状态
    loading,
    saving,
    testingText,
    testingImage,
    feedback,

    // 配置数据
    textConfig,
    imageConfig,

    // 文本服务商弹窗
    showTextModal,
    editingTextProvider,
    textForm,

    // 图片服务商弹窗
    showImageModal,
    editingImageProvider,
    imageForm,

    // 方法
    loadConfig,
    clearFeedback,

    // 文本服务商方法
    activateTextProvider,
    openAddTextModal,
    openEditTextModal,
    closeTextModal,
    saveTextProvider,
    deleteTextProvider,
    testTextConnection,
    testTextProviderInList,
    updateTextForm,

    // 图片服务商方法
    activateImageProvider,
    openAddImageModal,
    openEditImageModal,
    closeImageModal,
    saveImageProvider,
    deleteImageProvider,
    testImageConnection,
    testImageProviderInList,
    updateImageForm
  }
}
