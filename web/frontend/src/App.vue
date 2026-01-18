<template>
  <router-view v-if="!showShell" />
  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="sidebar-inner panel sidebar-rail">
        <RouterLink class="rail-logo" to="/chats" :title="branding.data.name">
          <img v-if="branding.data.logo_url" :src="branding.data.logo_url" />
          <span v-else style="font-weight: 900;">{{ branding.data.name.slice(0, 1).toUpperCase() }}</span>
        </RouterLink>
        <div class="rail-nav">
          <RouterLink class="rail-btn" to="/chats" title="Чаты">
            <i class="pi pi-comments"></i>
          </RouterLink>
          <RouterLink v-if="auth.me?.role === 'admin'" class="rail-btn" to="/settings/branding" title="Настройки">
            <i class="pi pi-cog"></i>
          </RouterLink>
        </div>
      </div>
    </aside>
    <div class="main">
      <header class="topbar">
        <div style="display:flex; flex-direction:column; gap:2px; min-width:0;">
          <div style="display:flex; align-items:center; gap:10px; min-width:0;">
            <div style="font-weight: 900; letter-spacing: -0.02em; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
              <span v-for="(p, idx) in brandParts" :key="idx" :style="{ color: p.color || 'inherit' }">{{ p.text }}</span>
            </div>
            <div v-if="pageLabel" class="muted" style="font-size: 12px; padding: 3px 10px; border: 1px solid var(--app-border); border-radius: 999px;">
              {{ pageLabel }}
            </div>
          </div>
          <div class="muted" style="font-size: 12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
            {{ branding.data.tagline }}
          </div>
        </div>
        <div class="btn-row">
          <Button :icon="ui.theme === 'dark' ? 'pi pi-moon' : 'pi pi-sun'" text rounded @click="toggleTheme" />
          <div v-if="auth.me" class="btn-row">
            <Avatar :label="auth.me.username.slice(0, 1).toUpperCase()" shape="circle" />
            <div style="display:flex; flex-direction:column; line-height: 1.1">
              <div style="font-weight:600">{{ auth.me.username }}</div>
              <div class="muted" style="font-size:12px">{{ auth.me.role }}</div>
            </div>
            <Button icon="pi pi-sign-out" text rounded @click="doLogout" />
          </div>
        </div>
      </header>
      <main class="content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Avatar from 'primevue/avatar'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { useBrandingStore } from '@/stores/branding'
import { parseColoredText } from '@/utils/coloredText'

const auth = useAuthStore()
const ui = useUiStore()
const branding = useBrandingStore()
const route = useRoute()
const router = useRouter()

onMounted(async () => {
  ui.applyTheme()
  await auth.fetchMe()
  if (!auth.me && route.path !== '/login') {
    router.push('/login')
    return
  }
  await branding.load()
})

const showShell = computed(() => Boolean(auth.me) && route.path !== '/login')
const brandParts = computed(() => parseColoredText(branding.data.name))

const pageLabel = computed(() => {
  if (route.path === '/login') return ''
  if (route.path.startsWith('/settings')) return 'Настройки'
  if (route.path.startsWith('/chats')) return 'Чаты'
  return ''
})

function toggleTheme() {
  ui.toggleTheme()
}

async function doLogout() {
  await auth.logout()
  router.push('/login')
}
</script>

<style scoped>
</style>
