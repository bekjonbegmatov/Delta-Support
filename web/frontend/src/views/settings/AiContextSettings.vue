<template>
  <div>
    <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom: 12px;">
      <div>
        <div style="font-size: 18px; font-weight: 700;">AI контекст</div>
        <div class="muted" style="font-size: 13px;">FAQ, тарифы, инструкции и особенности</div>
      </div>
      <div class="btn-row">
        <Button v-if="auth.isAdmin" label="Сбросить на .env" icon="pi pi-replay" outlined :loading="saving" @click="reset" />
        <Button v-if="auth.isAdmin" label="Сохранить" icon="pi pi-check" :loading="saving" @click="save" />
      </div>
    </div>

    <div class="panel card" style="padding: 14px; border-radius: 16px;">
      <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 14px;">
        <div>
          <div class="muted" style="font-size: 12px; margin-bottom: 6px;">FAQ</div>
          <Textarea v-model="form.service_faq" rows="8" style="width:100%" />
        </div>
        <div>
          <div class="muted" style="font-size: 12px; margin-bottom: 6px;">Тарифы</div>
          <Textarea v-model="form.service_tariffs" rows="8" style="width:100%" />
        </div>
        <div>
          <div class="muted" style="font-size: 12px; margin-bottom: 6px;">Инструкции</div>
          <Textarea v-model="form.service_instructions" rows="8" style="width:100%" />
        </div>
        <div>
          <div class="muted" style="font-size: 12px; margin-bottom: 6px;">Особенности</div>
          <Textarea v-model="form.service_features" rows="8" style="width:100%" />
        </div>
      </div>

      <div style="margin-top: 14px;">
        <div class="muted" style="font-size: 12px; margin-bottom: 6px;">Часы работы поддержки</div>
        <InputText v-model="form.service_support_hours" style="width:100%" />
      </div>

      <div v-if="err" style="color:#ef4444; font-size:13px; margin-top: 10px;">{{ err }}</div>
      <div v-if="hint" class="muted" style="font-size:12px; margin-top: 10px;">{{ hint }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import Button from 'primevue/button'
import Textarea from 'primevue/textarea'
import InputText from 'primevue/inputtext'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const saving = ref(false)
const err = ref('')
const hint = ref('')
const form = ref({
  service_faq: '',
  service_tariffs: '',
  service_instructions: '',
  service_features: '',
  service_support_hours: ''
})

async function load() {
  err.value = ''
  hint.value = ''
  const res = await fetch('/api/settings/ai-context', { credentials: 'include' })
  if (!res.ok) {
    const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
    err.value = data.detail || 'Ошибка'
    return
  }
  const data = await res.json()
  form.value = { ...data.effective }
  hint.value = 'Сохранённые значения хранятся в базе и применяются для ответов AI.'
}

async function save() {
  err.value = ''
  saving.value = true
  try {
    const res = await fetch('/api/settings/ai-context', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(form.value)
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
      throw new Error(data.detail || 'Ошибка')
    }
    await load()
  } catch (e: any) {
    err.value = e?.message || 'Ошибка'
  } finally {
    saving.value = false
  }
}

async function reset() {
  err.value = ''
  saving.value = true
  try {
    const res = await fetch('/api/settings/ai-context/reset', { method: 'POST', credentials: 'include' })
    if (!res.ok) {
      const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
      throw new Error(data.detail || 'Ошибка')
    }
    await load()
  } catch (e: any) {
    err.value = e?.message || 'Ошибка'
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>
