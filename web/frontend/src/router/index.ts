import { createRouter, createWebHistory } from 'vue-router'
import Chats from '@/views/Chats.vue'
import Login from '@/views/Login.vue'
import SettingsLayout from '@/views/settings/SettingsLayout.vue'
import BrandingSettings from '@/views/settings/BrandingSettings.vue'
import AiContextSettings from '@/views/settings/AiContextSettings.vue'
import KnowledgeBaseSettings from '@/views/settings/KnowledgeBaseSettings.vue'
import MediaSettings from '@/views/settings/MediaSettings.vue'
import ProfileSettings from '@/views/settings/ProfileSettings.vue'
import UsersSettings from '@/views/settings/UsersSettings.vue'
import TelegramSettings from '@/views/settings/TelegramSettings.vue'
import BotSettings from '@/views/settings/BotSettings.vue'
import NotFound from '@/views/NotFound.vue'

async function isAuthed() {
  const res = await fetch('/api/auth/me', { credentials: 'include' })
  return res.ok
}

async function getMe() {
  const res = await fetch('/api/auth/me', { credentials: 'include' })
  if (!res.ok) return null
  return await res.json()
}

const routes = [
  { path: '/', redirect: '/chats' },
  { path: '/login', component: Login },
  { path: '/chats/:id?', component: Chats },
  {
    path: '/settings',
    component: SettingsLayout,
    children: [
      { path: '', redirect: '/settings/profile' },
      { path: 'branding', component: BrandingSettings },
      { path: 'telegram', component: TelegramSettings },
      { path: 'bot', component: BotSettings },
      { path: 'ai', component: AiContextSettings },
      { path: 'kb', component: KnowledgeBaseSettings },
      { path: 'media', component: MediaSettings },
      { path: 'profile', component: ProfileSettings },
      { path: 'users', component: UsersSettings }
    ]
  },
  { path: '/:pathMatch(.*)*', component: NotFound }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach(async (to) => {
  if (to.path === '/login') {
    try {
      if (await isAuthed()) return '/chats'
      return true
    } catch {
      return true
    }
  }
  try {
    const me = await getMe()
    if (!me) return '/login'
    if (to.path.startsWith('/settings') && me.role !== 'admin') return '/chats'
    return true
  } catch {
    return '/login'
  }
})

export default router
