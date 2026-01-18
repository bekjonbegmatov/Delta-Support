<template>
  <div class="login">
    <div class="login-shell panel">
      <div class="login-left">
        <div class="login-brand">
          <div class="login-logo">
            <img v-if="branding.data.logo_url" :src="branding.data.logo_url" />
            <span v-else>{{ branding.data.name.slice(0, 1).toUpperCase() }}</span>
          </div>
          <div style="min-width:0;">
            <div class="login-title">
              <span v-for="(p, idx) in brandParts" :key="idx" :style="{ color: p.color || 'inherit' }">{{ p.text }}</span>
            </div>
            <div class="login-sub">{{ branding.data.tagline }}</div>
          </div>
        </div>
        <div class="muted" style="font-size: 13px; margin-top: 14px; line-height: 1.45;">
          Войдите, чтобы управлять чатами, операторами и настройками поддержки.
        </div>
      </div>


      <div class="login-right">
        <div style="font-weight: 900; font-size: 18px; letter-spacing: -0.02em;">Вход</div>
        <div class="muted" style="font-size: 13px; margin-top: 6px;">Введите логин и пароль</div>
        <br>

        <div style="display:flex; flex-direction:column; gap:10px; margin-top: 16px;">
          <span class="p-input-icon-left" style="width: 100%;">
            <i class="pi pi-user"></i>
            <InputText v-model="username" style="width: 100%;" placeholder="Имя пользователя" autocomplete="username" />
          </span>
          <span class="p-input-icon-left" style="width: 100%;">
            <i class="pi pi-lock"></i>
            <Password v-model="password" style="width: 100%;" placeholder="Пароль" :feedback="false" toggleMask inputClass="w-full" />
          </span>
          <br>
          <Button label="Войти" icon="pi pi-sign-in" :loading="loading" style="width: 100%;" @click="login" />
          <div v-if="error" class="error">{{ error }}</div>
          <div class="muted" style="font-size: 12px; margin-top: 6px;">

          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import Password from 'primevue/password'
import { useAuthStore } from '@/stores/auth'
import { useBrandingStore } from '@/stores/branding'
import { parseColoredText } from '@/utils/coloredText'

const router = useRouter()
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)
const auth = useAuthStore()
const branding = useBrandingStore()
const brandParts = computed(() => parseColoredText(branding.data.name))

onMounted(() => {
  branding.load()
})

async function login(){
  error.value = ''
  loading.value = true
  try{
    await auth.login(username.value, password.value)
    router.push('/chats')
  } catch (e: any) {
    error.value = e?.message || 'Ошибка входа'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login { display:flex; align-items:center; justify-content:center; height:100%; padding: 16px; }
.login-shell { width: min(920px, 100%); border-radius: 22px; overflow:hidden; display:grid; grid-template-columns: 1fr 420px; }
.login-left { padding: 22px; border-right: 1px solid var(--app-border); background: linear-gradient(135deg, rgba(34,197,94,0.12), rgba(56,189,248,0.10)); }
.login-right { padding: 22px; background: var(--app-panel); }
.login-brand { display:flex; align-items:center; gap: 12px; }
.login-logo { width: 56px; height: 56px; border-radius: 18px; border: 1px solid var(--app-border); background: rgba(34,197,94,0.10); display:flex; align-items:center; justify-content:center; overflow:hidden; font-weight: 900; font-size: 20px; }
.login-logo img { width: 100%; height: 100%; object-fit: cover; }
.login-title { font-weight: 950; font-size: 18px; letter-spacing: -0.02em; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.login-sub { font-size: 12px; color: var(--app-muted); margin-top: 2px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.error { color: #ef4444; font-size: 13px; }

.login-right :deep(.p-input-icon-left) {
  position: relative;
  display: block;
}

.login-right :deep(.p-input-icon-left > i) {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 2;
}

.login-right :deep(.p-input-icon-left > .p-inputtext),
.login-right :deep(.p-input-icon-left .p-password-input) {
  padding-left: 40px;
}

.login-right :deep(.p-input-icon-left .p-password) {
  width: 100%;
}

.login-right :deep(.p-input-icon-left .p-icon-field) {
  position: relative;
  width: 100%;
}

.login-right :deep(.p-input-icon-left .p-password-input) {
  width: 100%;
  padding-right: 44px;
}

.login-right :deep(.p-input-icon-left .p-icon-field > .p-input-icon) {
  position: absolute;
  right: 12px;
  top: 50%;
  display: block;
}

@media (max-width: 860px) {
  .login-shell { grid-template-columns: 1fr; }
  .login-left { border-right: none; border-bottom: 1px solid var(--app-border); }
}
</style>
