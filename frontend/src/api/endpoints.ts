import { request } from './client'
import type {
  Balance,
  DepositAddress,
  InternalTransfer,
  Page,
  SavedAddress,
  Transaction,
  TwoFactorSetup,
  User,
  Withdrawal,
} from './types'

// ------------------------------------------------------------------ account
export const login = (email: string, password: string, otp_code?: string) =>
  request<{ token: string; user: User }>('/account/login/', {
    method: 'POST',
    body: { email, password, ...(otp_code ? { otp_code } : {}) },
    anonymous: true,
  })

export const logout = () => request<{ success: true }>('/account/logout/', { method: 'POST' })

export const register = (data: { email: string; first_name: string; last_name: string; password: string }) =>
  request<{ id: number; email: string }>('/account/register/', { method: 'POST', body: data, anonymous: true })

export const resendVerification = (email: string) =>
  request('/account/resend-verification/', { method: 'POST', body: { email }, anonymous: true })

export const forgotPassword = (email: string) =>
  request('/account/password/forgot/', { method: 'POST', body: { email }, anonymous: true })

export const resetPassword = (uid: string, token: string, new_password: string) =>
  request('/account/password/reset/', { method: 'POST', body: { uid, token, new_password }, anonymous: true })

export const getMe = () => request<User>('/account/me/')

export const updateMe = (data: Pick<User, 'first_name' | 'last_name'>) =>
  request<User>('/account/me/', { method: 'PATCH', body: data })

export const changePassword = (old_password: string, new_password: string) =>
  request<{ token: string }>('/account/me/password/', { method: 'POST', body: { old_password, new_password } })

export const startTwoFactorSetup = (password: string) =>
  request<TwoFactorSetup>('/account/2fa/setup/', { method: 'POST', body: { password } })

export const enableTwoFactor = (otp_code: string) =>
  request<{ user: User }>('/account/2fa/enable/', { method: 'POST', body: { otp_code } })

export const disableTwoFactor = (password: string, otp_code: string) =>
  request<{ user: User }>('/account/2fa/disable/', { method: 'POST', body: { password, otp_code } })

export const listSavedAddresses = () => request<SavedAddress[]>('/account/withdrawal-addresses/')

export const addSavedAddress = (label: string, address: string, otp_code: string) =>
  request<SavedAddress>('/account/withdrawal-addresses/', { method: 'POST', body: { label, address, otp_code } })

export const deleteSavedAddress = (id: number) =>
  request<void>(`/account/withdrawal-addresses/${id}/`, { method: 'DELETE' })

export const setWhitelistMode = (enabled: boolean, otp_code?: string) =>
  request<{ user: User }>('/account/withdrawal-whitelist/', {
    method: 'POST',
    body: { enabled, ...(otp_code ? { otp_code } : {}) },
  })

// ------------------------------------------------------------------ wallet
export const getBalances = () => request<Balance[]>('/blockchain/balances/')

export const getDepositAddress = () => request<DepositAddress>('/blockchain/deposit-address/')

export interface TransactionFilters {
  page?: number
  page_size?: number
  direction?: string
  coin_info?: number | ''
  status?: string
  created_after?: string
  created_before?: string
}

export const getTransactions = (filters: TransactionFilters) =>
  request<Page<Transaction>>('/blockchain/transactions/', { query: { ...filters } })

export const getWithdrawals = (page_size = 5) =>
  request<Page<Withdrawal>>('/blockchain/withdrawals/', { query: { page_size } })

/** A withdrawal to another user's deposit address may come back as an instant internal transfer. */
export const createWithdrawal = (data: { coin_info: number; to_address: string; amount: string; otp_code: string }) =>
  request<Withdrawal | InternalTransfer>('/blockchain/withdrawals/', { method: 'POST', body: data })

export const getTransfers = (page_size = 5) =>
  request<Page<InternalTransfer>>('/blockchain/transfers/', { query: { page_size } })

export const createTransfer = (data: { coin_info: number; to: string; amount: string; memo: string; otp_code: string }) =>
  request<InternalTransfer>('/blockchain/transfers/', { method: 'POST', body: data })
