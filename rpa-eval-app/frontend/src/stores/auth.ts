import { defineStore } from 'pinia'
import { apiClient, type User } from '@/api/client'

interface AuthState {
  token: string
  user: User | null
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({
    token: localStorage.getItem('rpa_eval_token') || '',
    user: null
  }),
  getters: {
    isAuthenticated: (state) => Boolean(state.token),
    roleLabel: (state) => {
      const map: Record<string, string> = {
        admin: '系统管理员',
        buyer: '采购专员',
        approver: '合规审批经理'
      }
      return state.user ? map[state.user.role] || state.user.role : ''
    }
  },
  actions: {
    async login(username: string, password: string) {
      const { data } = await apiClient.post<{ access_token: string }>('/auth/login', { username, password })
      this.token = data.access_token
      localStorage.setItem('rpa_eval_token', data.access_token)
      await this.fetchMe()
    },
    async fetchMe() {
      if (!this.token) return
      const { data } = await apiClient.get<User>('/auth/me')
      this.user = data
    },
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem('rpa_eval_token')
    }
  }
})
