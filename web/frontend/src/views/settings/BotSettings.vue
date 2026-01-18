<template>
  <div>
    <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom: 12px;">
      <div>
        <div style="font-size: 18px; font-weight: 700;">Bot</div>
        <div class="muted" style="font-size: 13px;">Проект, приветствие и AI настройки</div>
      </div>
      <Button v-if="auth.isAdmin" label="Сохранить" icon="pi pi-check" :loading="saving" @click="save" />
    </div>

    <div class="panel card" style="padding: 14px; border-radius: 16px; max-width: 100%; width: 100%;">
      <div style="display:flex; flex-direction:column; gap: 14px;">
        <div class="panel" style="border-radius: 16px; padding: 12px;">
          <div class="muted" style="font-size: 12px; margin-bottom: 10px;">Проект (.env по умолчанию, приоритет у БД)</div>
          <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 10px;">
            <div style="grid-column: 1 / -1;">
              <div class="muted" style="font-size: 12px; margin-bottom: 6px;">Название проекта</div>
              <InputText v-model="form.project_name" style="width: 100%;" />
            </div>
            <div style="grid-column: 1 / -1;">
              <div class="muted" style="font-size: 12px; margin-bottom: 6px;">Описание</div>
              <Textarea v-model="form.project_description" rows="6" style="min-height: 160px; resize: vertical; width: 100%;" />
            </div>
            <div>
              <div class="muted" style="font-size: 12px; margin-bottom: 6px;">Сайт</div>
              <InputText v-model="form.project_website" style="width: 100%;" />
            </div>
            <div>
              <div class="muted" style="font-size: 12px; margin-bottom: 6px;">Ссылка на бота</div>
              <InputText v-model="form.project_bot_link" style="width: 100%;" />
            </div>
            <div style="grid-column: 1 / -1;">
              <div class="muted" style="font-size: 12px; margin-bottom: 6px;">Контакты владельца</div>
              <InputText v-model="form.project_owner_contacts" style="width: 100%;" />
            </div>
          </div>
        </div>

        <div class="panel" style="border-radius: 16px; padding: 12px;">
          <div class="muted" style="font-size: 12px; margin-bottom: 10px;">Приветственное сообщение</div>
          <div class="muted" style="font-size: 12px; margin-bottom: 6px; display:flex; align-items:center; gap:8px;">
            <span>Шаблон</span>
            <HelpTip :text="welcomeHelp" />
          </div>
          <Textarea v-model="form.bot_welcome_message" rows="10" style="min-height: 260px; resize: vertical; width: 100%;" />
        </div>

        <div class="panel" style="border-radius: 16px; padding: 12px;">
          <div class="muted" style="font-size: 12px; margin-bottom: 10px;">AI</div>
          <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 10px;">
            <div>
              <div class="muted" style="font-size: 12px; margin-bottom: 6px;">API тип</div>
              <InputText v-model="form.ai_support_api_type" placeholder="groq" style="width: 100%;" />
            </div>
            <div>
              <div class="muted" style="font-size: 12px; margin-bottom: 6px;">Модели (через запятую)</div>
              <InputText v-model="form.groq_models" placeholder="llama-3.1-8b-instant,..." style="width: 100%;" />
            </div>
            <div style="grid-column: 1 / -1;">
              <div class="muted" style="font-size: 12px; margin-bottom: 6px; display:flex; align-items:center; gap:8px;">
                <span>System prompt (глобальная инструкция)</span>
                <HelpTip :text="promptHelp" />
              </div>
              <Textarea v-model="form.ai_system_prompt" rows="18" style="min-height: 360px; resize: vertical; width: 100%;" />
            </div>

            <div style="grid-column: 1 / -1;">
              <div class="muted" style="font-size: 12px; margin-bottom: 8px; display:flex; align-items:center; justify-content:space-between; gap: 10px;">
                <div style="display:flex; align-items:center; gap:8px;">
                  <span>API ключи</span>
                  <span class="muted" style="font-size: 12px;">({{ secrets.ai_support_api_key_set || secrets.ai_support_api_keys_set ? 'есть сохранённые' : 'не заданы' }})</span>
                </div>
                <Button size="small" label="Добавить ключ" icon="pi pi-plus" text @click="addKey" />
              </div>
              <div v-if="secretsPreview.ai_support_api_keys_count" class="muted" style="font-size: 12px; margin-bottom: 10px;">
                Сохранено: {{ secretsPreview.ai_support_api_keys.join(', ') }}
              </div>
              <div style="display:flex; flex-direction:column; gap: 8px;">
                <div v-for="(_, idx) in keys" :key="idx" style="display:flex; gap:10px; align-items:center;">
                  <InputText v-model="keys[idx]" type="password" placeholder="Ключ" style="width: 100%;" />
                  <Button size="small" icon="pi pi-trash" text @click="removeKey(idx)" />
                </div>
              </div>
              <label v-if="secretsPreview.ai_support_api_keys_count && !clearAllKeys" style="display:flex; align-items:center; gap:10px; margin-top: 10px;">
                <input v-model="appendKeys" type="checkbox" />
                <span>Добавлять к сохранённым (а не заменять)</span>
              </label>
              <label style="display:flex; align-items:center; gap:10px; margin-top: 10px;">
                <input v-model="clearAllKeys" type="checkbox" />
                <span>Очистить сохранённые ключи</span>
              </label>
            </div>
          </div>
        </div>

        <div v-if="err" style="color:#ef4444; font-size: 13px;">{{ err }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Textarea from 'primevue/textarea'
import { useAuthStore } from '@/stores/auth'
import HelpTip from '@/components/HelpTip.vue'

const auth = useAuthStore()
const saving = ref(false)
const err = ref('')
const form = ref({
  project_name: '',
  project_description: '',
  project_website: '',
  project_bot_link: '',
  project_owner_contacts: '',
  bot_welcome_message: '',
  ai_system_prompt: '',
  ai_support_api_type: 'groq',
  groq_models: ''
})
const secrets = ref({ ai_support_api_key_set: false, ai_support_api_keys_set: false })
const secretsPreview = ref({ ai_support_api_keys: [] as string[], ai_support_api_keys_count: 0 })
const keys = ref<string[]>([''])
const clearAllKeys = ref(false)
const appendKeys = ref(true)

const welcomeHelp =
  'Переменные:\n' +
  '{first_name}, {last_name}, {username}, {user_id}\n' +
  '{project_name}, {project_description}, {project_website}, {project_bot_link}, {project_owner_contacts}'

const promptHelp =
  'Переменные:\n' +
  '{project_name}, {service_context}, {first_name}, {last_name}, {username}, {user_id}'

async function load() {
  err.value = ''
  const res = await fetch('/api/settings/bot', { credentials: 'include' })
  if (!res.ok) {
    const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
    err.value = data.detail || 'Ошибка'
    return
  }
  const data = await res.json()
  form.value = { ...form.value, ...(data.effective || {}) }
  secrets.value = data.secrets || secrets.value
  secretsPreview.value = data.secrets_preview || secretsPreview.value
  keys.value = ['']
  clearAllKeys.value = false
  appendKeys.value = true
}

function addKey() {
  keys.value.push('')
}

function removeKey(idx: number) {
  keys.value.splice(idx, 1)
  if (!keys.value.length) keys.value = ['']
}

async function save() {
  err.value = ''
  saving.value = true
  try {
    const payload: any = { ...form.value }
    if (clearAllKeys.value) {
      payload.ai_support_api_keys = ''
    } else {
      const cleaned = keys.value.map((k) => k.trim()).filter(Boolean)
      if (cleaned.length) {
        payload.ai_support_api_keys = cleaned.join(',')
        if (appendKeys.value && secretsPreview.value.ai_support_api_keys_count) payload.ai_support_api_keys_append = true
      }
    }
    const res = await fetch('/api/settings/bot', {
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
