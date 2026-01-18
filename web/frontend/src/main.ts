import { createApp } from 'vue'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import 'primeicons/primeicons.css'
import 'primevue/resources/themes/lara-light-teal/theme.css'
import 'primevue/resources/primevue.min.css'
import './styles/app.css'
import App from './App.vue'
import router from './router'

function shouldIgnoreCheckoutPopupError(value: unknown): boolean {
  const msg =
    typeof value === 'string'
      ? value
      : typeof value === 'object' && value && 'message' in value && typeof (value as any).message === 'string'
        ? (value as any).message
        : ''
  return msg.includes('No checkout popup config found')
}

window.addEventListener('unhandledrejection', (event) => {
  if (shouldIgnoreCheckoutPopupError(event.reason)) {
    event.preventDefault()
  }
})

window.addEventListener('error', (event) => {
  if (shouldIgnoreCheckoutPopupError((event as any).error) || shouldIgnoreCheckoutPopupError((event as any).message)) {
    event.preventDefault()
  }
})

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(PrimeVue, { ripple: true })
app.mount('#app')
