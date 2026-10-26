import axios from 'axios'
import { API_BASE_URL } from './client'
import type { AntigravityAccountsResponse, AntigravityLoginStatus, AntigravityModels, AntigravityQuota } from './types'

/**
 * Google Antigravity（Cloud Code Assist）OAuth 登录 API
 *
 * 登录流程由后端启动本地回调服务并打开浏览器，
 * 前端轮询 status 直到 connected / failed。
 */

export async function startAntigravityLogin(): Promise<{
  success: boolean
  state?: string
  auth_url?: string
  redirect_uri?: string
  message?: string
}> {
  const response = await axios.post(`${API_BASE_URL}/auth/antigravity/login`)
  return response.data
}

export async function getAntigravityStatus(): Promise<AntigravityLoginStatus> {
  const response = await axios.get(`${API_BASE_URL}/auth/antigravity/status`)
  return response.data
}

export async function cancelAntigravityLogin(): Promise<{ success: boolean; message?: string }> {
  const response = await axios.post(`${API_BASE_URL}/auth/antigravity/cancel`)
  return response.data
}

export async function logoutAntigravity(): Promise<{ success: boolean; message?: string }> {
  const response = await axios.post(`${API_BASE_URL}/auth/antigravity/logout`)
  return response.data
}

export async function getAntigravityAccounts(): Promise<AntigravityAccountsResponse> {
  const response = await axios.get(`${API_BASE_URL}/auth/antigravity/accounts`)
  return response.data
}

export async function logoutAntigravityAccount(accountId: string): Promise<{ success: boolean; message?: string }> {
  const response = await axios.post(`${API_BASE_URL}/auth/antigravity/account/logout`, { account_id: accountId })
  return response.data
}

export async function unblockAntigravityAccount(accountId: string): Promise<{ success: boolean; message?: string }> {
  const response = await axios.post(`${API_BASE_URL}/auth/antigravity/account/unblock`, { account_id: accountId })
  return response.data
}

export async function getAntigravityModels(): Promise<AntigravityModels> {
  const response = await axios.get(`${API_BASE_URL}/auth/antigravity/models`)
  return response.data
}

export async function getAntigravityQuota(): Promise<AntigravityQuota> {
  const response = await axios.get(`${API_BASE_URL}/auth/antigravity/quota`)
  return response.data
}
