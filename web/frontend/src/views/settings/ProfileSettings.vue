<template>
  <div>
    <div style="display:flex; align-items:center; justify-content:space-between; gap: 12px; margin-bottom: 12px;">
      <div>
        <div style="font-size: 18px; font-weight: 700;">Профиль</div>
        <div class="muted" style="font-size: 13px;">Смена пароля и данные аккаунта</div>
      </div>
      <div v-if="auth.me" class="muted" style="font-size: 13px;">
        {{ auth.me.username }} · {{ auth.me.role }}
      </div>
    </div>

    <div class="panel card" style="padding: 14px; border-radius: 16px; max-width: 720px;">
      <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 14px;">
        <div style="display:flex; flex-direction:column; gap: 10px;">
          <div class="muted" style="font-size: 12px;">Аккаунт</div>
          <InputText v-model="username" placeholder="Username" />
        </div>

        <div style="display:flex; flex-direction:column; gap: 10px;">
          <div class="muted" style="font-size: 12px;">Смена пароля</div>
          <Password v-model="oldPassword" placeholder="Старый пароль" :feedback="false" toggleMask />
          <Password v-model="newPassword" placeholder="Новый пароль (мин 8 символов)" :feedback="false" toggleMask />
        </div>
      </div>
      <div style="display:flex; justify-content:flex-end; margin-top: 14px;">
        <Button label="Сохранить" icon="pi pi-check" :loading="saving" @click="saveAll" />
      </div>
      <div v-if="msg" class="muted" style="font-size: 13px; margin-top: 12px;">{{ msg }}</div>
      <div v-if="err" style="color:#ef4444; font-size: 13px; margin-top: 8px;">{{ err }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import Password from 'primevue/password'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const username = ref(auth.me?.username || '')
const oldPassword = ref('')
const newPassword = ref('')
const saving = ref(false)
const err = ref('')
const msg = ref('')

async function saveAll() {
  err.value = ''
  msg.value = ''
  saving.value = true
  try {
    const actions: string[] = []
    const currentUsername = auth.me?.username || ''
    const nextUsername = username.value.trim()
    if (nextUsername && nextUsername !== currentUsername) {
      const res = await fetch('/api/auth/change-username', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ new_username: nextUsername })
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
        throw new Error(data.detail || 'Ошибка')
      }
      await auth.fetchMe()
      username.value = auth.me?.username || username.value
      actions.push('Username обновлён')
    }
    if (oldPassword.value || newPassword.value) {
      if (!oldPassword.value || !newPassword.value) {
        throw new Error('Введите старый и новый пароль')
      }
      const res = await fetch('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ old_password: oldPassword.value, new_password: newPassword.value })
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
        throw new Error(data.detail || 'Ошибка')
      }
      oldPassword.value = ''
      newPassword.value = ''
      actions.push('Пароль обновлён')
    }
    msg.value = actions.length ? actions.join(' · ') : 'Нет изменений'
  } catch (e: any) {
    err.value = e?.message || 'Ошибка'
  } finally {
    saving.value = false
  }
}
</script>
