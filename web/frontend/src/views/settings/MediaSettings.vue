<template>
  <div>
    <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom: 12px;">
      <div>
        <div style="font-size: 18px; font-weight: 700;">Медиа</div>
        <div class="muted" style="font-size: 13px;">Отправка файлов и политика хранения</div>
      </div>
      <Button v-if="auth.isAdmin" label="Сохранить" icon="pi pi-check" :loading="saving" @click="save" />
    </div>

    <div class="panel card" style="padding: 14px; border-radius: 16px; max-width: 720px;">
      <div style="display:flex; flex-direction:column; gap: 12px;">
        <label style="display:flex; align-items:center; gap:10px;">
          <input v-model="form.keep_forever" type="checkbox" />
          <span>Не удалять медиа</span>
        </label>
        <div>
          <div class="muted" style="font-size: 12px; margin-bottom: 6px;">Удалять медиа через N дней (если не включено “не удалять”)</div>
          <InputText v-model="daysStr" placeholder="Например: 30" :disabled="form.keep_forever" />
        </div>
        <div v-if="err" style="color:#ef4444; font-size: 13px;">{{ err }}</div>
        <div class="muted" style="font-size: 12px;">
          Удаление физически будет добавлено отдельной задачей (планировщик). Сейчас это настройка хранения.
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

const auth = useAuthStore()
const saving = ref(false)
const err = ref('')
const form = ref<{ keep_forever: boolean; retention_days: number | null }>({ keep_forever: true, retention_days: null })

const daysStr = computed({
  get: () => (form.value.retention_days == null ? '' : String(form.value.retention_days)),
  set: (v: string) => {
    const t = v.trim()
    if (!t) {
      form.value.retention_days = null
      return
    }
    const n = Number(t)
    form.value.retention_days = Number.isFinite(n) ? Math.trunc(n) : null
  }
})

async function load() {
  err.value = ''
  const res = await fetch('/api/settings/media', { credentials: 'include' })
  if (!res.ok) {
    const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
    err.value = data.detail || 'Ошибка'
    return
  }
  const data = await res.json()
  form.value = { keep_forever: Boolean(data.keep_forever), retention_days: data.retention_days ?? null }
}

async function save() {
  err.value = ''
  saving.value = true
  try {
    const payload = {
      keep_forever: form.value.keep_forever,
      retention_days: form.value.keep_forever ? null : form.value.retention_days
    }
    const res = await fetch('/api/settings/media', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(payload)
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

onMounted(load)
</script>

