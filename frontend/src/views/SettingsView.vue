<template>
  <div class="container">
    <div class="page-header">
      <h1 class="page-title">Cài đặt hệ thống</h1>
      <p class="page-subtitle">Cấu hình dịch vụ API tạo văn bản và tạo hình ảnh</p>
    </div>

    <div v-if="loading" class="loading-container">
      <div class="spinner"></div>
      <p>Đang tải cấu hình...</p>
    </div>

    <div v-else class="settings-container">
      <AntigravityAuthCard
        :connected="authConnected"
        :loggingIn="authLoggingIn"
        :flow="authFlow"
        :message="authMessage"
        :authUrl="authUrl"
        :credential="authCredential"
        :accounts="authAccounts"
        :textModels="authTextModels"
        :imageModels="authImageModels"
        :modelsLoading="authModelsLoading"
        @login="startAuthLogin"
        @logout="onAuthLogout"
        @cancel="cancelAuthLogin"
        @logout-account="onAuthLogoutAccount"
        @unblock="onAuthUnblockAccount"
      />
      <ErrorCard
        v-if="feedback?.type === 'error'"
        :error="feedback.error"
        dismissible
        @dismiss="clearFeedback"
        style="margin-bottom: 16px;"
      />

      <div
        v-else-if="feedback?.type === 'success'"
        class="success-card"
        role="status"
        aria-live="polite"
      >
        <span>{{ feedback.message }}</span>
        <button type="button" @click="clearFeedback" aria-label="Đóng thông báo">×</button>
      </div>

      <!-- 文本生成配置 -->
      <div class="card">
        <div class="section-header">
          <div>
            <h2 class="section-title">Cấu hình tạo văn bản</h2>
            <p class="section-desc">Dùng để tạo dàn ý bài viết Xiaohongshu</p>
          </div>
          <button class="btn btn-small" @click="openAddTextModal">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="12" y1="5" x2="12" y2="19"></line>
              <line x1="5" y1="12" x2="19" y2="12"></line>
            </svg>
            Thêm
          </button>
        </div>

        <!-- 服务商列表表格 -->
        <ProviderTable
          :providers="textConfig.providers"
          :activeProvider="textConfig.active_provider"
          @activate="activateTextProvider"
          @edit="openEditTextModal"
          @delete="deleteTextProvider"
          @test="testTextProviderInList"
        />
      </div>

      <!-- 图片生成配置 -->
      <div class="card">
        <div class="section-header">
          <div>
            <h2 class="section-title">Cấu hình tạo hình ảnh</h2>
            <p class="section-desc">Dùng để tạo hình ảnh minh họa Xiaohongshu</p>
          </div>
          <button class="btn btn-small" @click="openAddImageModal">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="12" y1="5" x2="12" y2="19"></line>
              <line x1="5" y1="12" x2="19" y2="12"></line>
            </svg>
            Thêm
          </button>
        </div>

        <!-- 服务商列表表格 -->
        <ProviderTable
          :providers="imageConfig.providers"
          :activeProvider="imageConfig.active_provider"
          @activate="activateImageProvider"
          @edit="openEditImageModal"
          @delete="deleteImageProvider"
          @test="testImageProviderInList"
        />
      </div>
    </div>

    <!-- 文本服务商弹窗 -->
    <ProviderModal
      :visible="showTextModal"
      :isEditing="!!editingTextProvider"
      :formData="textForm"
      :testing="testingText"
      :typeOptions="textTypeOptions"
      providerCategory="text"
      :oauthState="authCredential"
      :modelSuggestions="authTextModels"
      @close="closeTextModal"
      @save="saveTextProvider"
      @test="testTextConnection"
      @update:formData="updateTextForm"
    />

    <!-- 图片服务商弹窗 -->
    <ImageProviderModal
      :visible="showImageModal"
      :isEditing="!!editingImageProvider"
      :formData="imageForm"
      :testing="testingImage"
      :typeOptions="imageTypeOptions"
      :oauthState="authCredential"
      :modelSuggestions="authImageModels"
      @close="closeImageModal"
      @save="saveImageProvider"
      @test="testImageConnection"
      @update:formData="updateImageForm"
    />
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import ProviderTable from '../components/settings/ProviderTable.vue'
import ProviderModal from '../components/settings/ProviderModal.vue'
import ImageProviderModal from '../components/settings/ImageProviderModal.vue'
import AntigravityAuthCard from '../components/settings/AntigravityAuthCard.vue'
import ErrorCard from '../components/common/ErrorCard.vue'
import {
  useProviderForm,
  textTypeOptions,
  imageTypeOptions
} from '../composables/useProviderForm'
import { useAntigravityAuth } from '../composables/useAntigravityAuth'

/**
 * 系统设置页面
 *
 * 功能：
 * - 管理文本生成服务商配置
 * - 管理图片生成服务商配置
 * - 测试 API 连接
 */

// 使用 composable 管理表单状态和逻辑
const {
  // 状态
  loading,
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
} = useProviderForm()

// Google 账号登录（Antigravity OAuth）：解构出 ref，模板才能自动解包
const {
  connected: authConnected,
  loggingIn: authLoggingIn,
  flow: authFlow,
  message: authMessage,
  authUrl,
  credential: authCredential,
  accounts: authAccounts,
  textModels: authTextModels,
  imageModels: authImageModels,
  modelsLoading: authModelsLoading,
  refreshStatus: refreshAuthStatus,
  startLogin: startAuthLogin,
  cancelLogin: cancelAuthLogin,
  logout: logoutAuth,
  logoutAccount: logoutAuthAccount,
  unblockAccount: unblockAuthAccount
} = useAntigravityAuth()

async function onAuthLogout() {
  await logoutAuth()
  await loadConfig()
}

async function onAuthLogoutAccount(accountId: string) {
  await logoutAuthAccount(accountId)
  await loadConfig()
}

async function onAuthUnblockAccount(accountId: string) {
  await unblockAuthAccount(accountId)
}

onMounted(() => {
  loadConfig()
  refreshAuthStatus()
})
</script>

<style scoped>
.settings-container {
  max-width: 900px;
  margin: 0 auto;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 20px;
}

.section-title {
  font-size: 18px;
  font-weight: 600;
  margin-bottom: 4px;
  color: #1a1a1a;
}

.section-desc {
  font-size: 14px;
  color: #666;
  margin: 0;
}

.success-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  padding: 12px 14px;
  border: 1px solid #bbf7d0;
  background: #f0fdf4;
  color: #166534;
  border-radius: 8px;
  font-size: 14px;
}

.success-card button {
  border: none;
  background: transparent;
  color: #166534;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
}

/* 按钮样式 */
.btn-small {
  padding: 6px 12px;
  font-size: 13px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

/* 加载状态 */
.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 20px;
  color: #666;
}
</style>
