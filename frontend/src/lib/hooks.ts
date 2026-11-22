import { useQuery } from '@tanstack/react-query'

import * as api from '../api/endpoints'

export const useBalances = () => useQuery({ queryKey: ['balances'], queryFn: api.getBalances })

export const useSavedAddresses = () =>
  useQuery({ queryKey: ['saved-addresses'], queryFn: api.listSavedAddresses })

/** Query keys to refresh after money moves. */
export const MONEY_KEYS = [['balances'], ['transactions'], ['withdrawals'], ['transfers']] as const
