<template>
  <div class="panel card" style="height: 100%; overflow: hidden;">
    <div style="display:grid; grid-template-columns: 260px 1fr; height:100%;">
      <div style="border-right: 1px solid var(--app-border); padding: 12px;">
        <div style="font-weight:700; margin: 4px 6px 12px;">Настройки</div>
        <div class="muted" style="font-size: 12px; margin: 0 6px 12px;">
          Профиль, пользователи, бот
        </div>
        <div style="display:flex; flex-direction:column; gap:6px;">
          <RouterLink class="s-item" to="/settings/branding"><i class="pi pi-palette" />Брендинг</RouterLink>
          <RouterLink class="s-item" to="/settings/bot"><i class="pi pi-send" />Bot</RouterLink>
          <RouterLink class="s-item" to="/settings/telegram"><i class="pi pi-comments" />Telegram топики</RouterLink>
          <RouterLink class="s-item" to="/settings/ai"><i class="pi pi-microchip" />AI контекст</RouterLink>
          <RouterLink class="s-item" to="/settings/kb"><i class="pi pi-book" />База знаний</RouterLink>
          <RouterLink class="s-item" to="/settings/media"><i class="pi pi-images" />Медиа</RouterLink>
          <RouterLink class="s-item" to="/settings/profile"><i class="pi pi-user" />Профиль</RouterLink>
          <RouterLink class="s-item" to="/settings/users"><i class="pi pi-users" />Пользователи</RouterLink>
        </div>
      </div>
      <div style="padding: 14px; overflow:auto;">
        <router-view />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

onMounted(() => {
  if (auth.me?.role !== 'admin') {
    router.replace('/chats')
  }
})
</script>

<style scoped>
.s-item{
  display:flex;
  align-items:center;
  gap:10px;
  padding:10px 12px;
  border-radius: 12px;
  color: var(--app-text);
  text-decoration:none;
  border: 1px solid transparent;
}
.s-item:hover{ background: rgba(148, 163, 184, 0.08); }
.router-link-active{
  background: rgba(34, 197, 94, 0.12);
  border-color: rgba(34, 197, 94, 0.20);
}
</style>
