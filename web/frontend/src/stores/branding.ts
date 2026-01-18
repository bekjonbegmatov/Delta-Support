import { defineStore } from 'pinia'

export type Branding = {
  name: string
  tagline: string
  logo_url: string
}

export const useBrandingStore = defineStore('branding', {
  state: () => ({
    data: { name: 'Support Desk', tagline: 'Админ‑панель поддержки', logo_url: '' } as Branding,
    loaded: false
  }),
  actions: {
    async load() {
      const res = await fetch('/api/branding', { credentials: 'include' })
      if (!res.ok) return
      this.data = await res.json()
      this.loaded = true
    },
    async save(next: Branding) {
      const res = await fetch('/api/branding', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(next)
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
        throw new Error(data.detail || 'Ошибка')
      }
      this.data = next
      this.loaded = true
    }
  }
})

