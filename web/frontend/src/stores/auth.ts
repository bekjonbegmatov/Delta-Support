import { defineStore } from 'pinia'

export type Me = { id: number; username: string; role: string }

export const useAuthStore = defineStore('auth', {
  state: () => ({
    me: null as Me | null,
    loading: false
  }),
  getters: {
    isAuthed: (s) => Boolean(s.me),
    isAdmin: (s) => s.me?.role === 'admin'
  },
  actions: {
    async fetchMe() {
      this.loading = true
      try {
        const res = await fetch('/api/auth/me', { credentials: 'include' })
        if (!res.ok) {
          this.me = null
          return null
        }
        this.me = await res.json()
        return this.me
      } finally {
        this.loading = false
      }
    },
    async login(username: string, password: string) {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ username, password })
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({ detail: 'Ошибка входа' }))
        throw new Error(data.detail || 'Ошибка входа')
      }
      await this.fetchMe()
    },
    async logout() {
      await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
      this.me = null
    }
  }
})

