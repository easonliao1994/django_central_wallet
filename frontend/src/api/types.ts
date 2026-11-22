export interface User {
  id: number
  email: string
  first_name: string
  last_name: string
  is_verified: boolean
  created_at: string
  two_factor_enabled: boolean
  withdrawal_whitelist_enabled: boolean
  whitelist_disable_at: string | null
  withdrawals_locked_until: string | null
}

export interface Balance {
  coin_info: number
  blockchain_id: number
  blockchain: string
  symbol: string
  name: string
  contract: string | null
  decimal: number
  balance: string
  balance_raw: string
}

export interface DepositAddress {
  address: string
  qr_code_svg: string
}

export type Direction = 'deposit' | 'withdrawal' | 'internal' | 'sweep' | 'gas_topup'
export type TxStatus = 'pending' | 'confirmed' | 'failed'
export type WithdrawalStatus =
  | 'pending_review'
  | 'approved'
  | 'broadcast'
  | 'stuck'
  | 'confirmed'
  | 'failed'
  | 'rejected'

export interface Transaction {
  id: number
  direction: Direction
  flow: 'in' | 'out' | null
  status: TxStatus
  blockchain_id: number | null
  blockchain: string | null
  coin_info: number | null
  symbol: string | null
  amount: string
  amount_raw: string
  from_address: string | null
  to_address: string | null
  tx_id: string | null
  tx_url: string | null
  block_number: number | null
  withdrawal: number | null
  withdrawal_status: WithdrawalStatus | null
  internal_transfer: number | null
  created_at: string
}

export interface Withdrawal {
  id: number
  blockchain: string
  symbol: string
  coin_info: number
  to_address: string
  amount: string
  status: WithdrawalStatus
  tx_id: string | null
  tx_url: string | null
  created_at: string
  updated_at: string
}

export interface InternalTransfer {
  id: number
  type: 'internal_transfer'
  flow: 'in' | 'out'
  blockchain: string
  symbol: string
  coin_info: number
  amount: string
  to: string | null
  my_address: string
  memo: string
  created_at: string
}

export interface SavedAddress {
  id: number
  label: string
  address: string
  created_at: string
  active_from: string
  active: boolean
}

export interface Page<T> {
  success: true
  count: number
  next: boolean
  results: T[]
}

export interface TwoFactorSetup {
  secret: string
  otpauth_uri: string
  qr_code_svg: string
}
