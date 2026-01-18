<template>
  <div>
    <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom: 12px;">
      <div>
        <div style="font-size: 18px; font-weight: 700;">Брендинг</div>
        <div class="muted" style="font-size: 13px;">Название панели и логотип</div>
      </div>
      <Button v-if="auth.isAdmin" label="Сохранить" icon="pi pi-check" :loading="saving" @click="save" />
    </div>

    <div class="panel card" style="padding: 14px; border-radius: 16px; max-width: 100%; width: 100%;">
      <div style="display:grid; grid-template-columns: 1fr 260px; gap: 14px; align-items:start;">
        <div style="display:flex; flex-direction:column; gap:10px;">
          <div class="muted" style="font-size:12px;">Название</div>
          <InputText v-model="form.name" placeholder="Например: {cyan}Delta {white}VPN Support" />
          <div class="muted" style="font-size:12px; line-height: 1.4; display:flex; align-items:center; gap: 8px;">
            <span>Можно окрасить слова через синтаксис {color}</span>
            <HelpTip :text="colorHelp" />
          </div>
          <div class="muted" style="font-size:12px;">Подзаголовок</div>
          <InputText v-model="form.tagline" placeholder="Например: Панель поддержки и чаты" />
          <div class="muted" style="font-size:12px;">URL логотипа</div>
          <InputText v-model="form.logo_url" placeholder="https://.../logo.png" />
          <input ref="logoInput" type="file" accept="image/png,image/jpeg,image/webp" style="display:none" @change="onLogoPicked" />
          <Button v-if="auth.isAdmin" label="Загрузить логотип" icon="pi pi-upload" outlined :loading="uploading" @click="pickLogo" />
          <div v-if="err" style="color:#ef4444; font-size:13px;">{{ err }}</div>
        </div>

        <div class="panel" style="border-radius: 16px; padding: 14px;">
          <div class="muted" style="font-size:12px; margin-bottom: 10px;">Превью</div>
          <div style="display:flex; align-items:center; gap: 10px;">
            <div v-if="form.logo_url" style="width: 42px; height: 42px; border-radius: 12px; overflow:hidden; border:1px solid var(--app-border); background: rgba(148,163,184,0.08);">
              <img :src="form.logo_url" style="width:100%; height:100%; object-fit: cover;" />
            </div>
            <div v-else style="width: 42px; height: 42px; border-radius: 12px; display:flex; align-items:center; justify-content:center; border:1px solid var(--app-border); background: rgba(34,197,94,0.10); font-weight:800;">
              {{ form.name.slice(0, 1).toUpperCase() }}
            </div>
            <div style="min-width: 0;">
              <div style="font-weight:800;">
                <span v-for="(p, idx) in nameParts" :key="idx" :style="{ color: p.color || 'inherit' }">{{ p.text }}</span>
              </div>
              <div class="muted" style="font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
                {{ form.tagline }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import { useAuthStore } from '@/stores/auth'
import { useBrandingStore } from '@/stores/branding'
import { parseColoredText } from '@/utils/coloredText'
import HelpTip from '@/components/HelpTip.vue'

const auth = useAuthStore()
const branding = useBrandingStore()
const saving = ref(false)
const uploading = ref(false)
const err = ref('')
const form = ref({ name: '', tagline: '', logo_url: '' })
const logoInput = ref<HTMLInputElement | null>(null)
const nameParts = computed(() => parseColoredText(form.value.name))
const colorHelp =
  'Поддерживает HEX и названия цветов.\n' +
  'Примеры:\n' +
  '{#B8F2E6}Del{#FFA69E}ta {#AEC6CF}VPN\n' +
  '{cyan}Delta {white}VPN\n' +
  '{#B8F2E6}Del{#FFA69E}ta {cyan}VPN'

async function load() {
  err.value = ''
  await branding.load()
  form.value = { ...branding.data }
}

async function save() {
  err.value = ''
  saving.value = true
  try {
    await branding.save({ ...form.value })
  } catch (e: any) {
    err.value = e?.message || 'Ошибка'
  } finally {
    saving.value = false
  }
}

function pickLogo() {
  err.value = ''
  logoInput.value?.click()
}

async function onLogoPicked(ev: Event) {
  const input = ev.target as HTMLInputElement | null
  const file = input?.files?.[0] || null
  if (input) input.value = ''
  if (!file) return
  err.value = ''
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch('/api/branding/logo', { method: 'POST', credentials: 'include', body: fd })
    if (!res.ok) {
      const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
      throw new Error(data.detail || 'Ошибка')
    }
    const data = await res.json()
    form.value.logo_url = data.logo_url
  } catch (e: any) {
    err.value = e?.message || 'Ошибка'
  } finally {
    uploading.value = false
  }
}

onMounted(load)
</script>
