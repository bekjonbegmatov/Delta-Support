import { defineStore } from 'pinia'

type ThemeMode = 'light' | 'dark'

export const useUiStore = defineStore('ui', {
  state: () => ({
    theme:
      (localStorage.getItem('theme') as ThemeMode) ||
      (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
  }),
  actions: {
    applyTheme() {
      document.body.classList.toggle('dark', this.theme === 'dark')
      localStorage.setItem('theme', this.theme)
    },
    toggleTheme() {
      this.theme = this.theme === 'dark' ? 'light' : 'dark'
      this.applyTheme()
    }
  }
})
