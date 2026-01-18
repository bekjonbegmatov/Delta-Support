<template>
  <div>
    <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom: 12px;">
      <div>
        <div style="font-size: 18px; font-weight: 700;">Telegram топики</div>
        <div class="muted" style="font-size: 13px;">Группа поддержки, шаблон названия и эмодзи</div>
      </div>
      <Button v-if="auth.isAdmin" label="Сохранить" icon="pi pi-check" :loading="saving" @click="save" />
    </div>

    <div class="panel card" style="padding: 14px; border-radius: 16px; max-width: 100%; width: 100%;">
      <div style="display:flex; flex-direction:column; gap: 14px;">
        <label style="display:flex; align-items:center; gap:10px;">
          <input v-model="form.telegram_group_mode" type="checkbox" />
          <span>Включить режим группы (форум‑топики)</span>
        </label>

        <div>
          <div class="muted" style="font-size: 12px; margin-bottom: 6px; display:flex; align-items:center; gap:8px;">
            <span>ID группы поддержки</span>
            <HelpTip :text="groupHelp" />
          </div>
          <InputText v-model="groupIdStr" placeholder="-1001234567890" style="width: 100%;" />
        </div>

        <div>
          <div class="muted" style="font-size: 12px; margin-bottom: 6px; display:flex; align-items:center; gap:8px;">
            <span>Шаблон названия топика</span>
            <HelpTip :text="templateHelp" />
          </div>
          <InputText v-model="form.telegram_topic_title_template" placeholder="{emoji} {first_name} ({user_id}) {status_label}" style="width: 100%;" />
        </div>

        <div class="panel" style="border-radius: 16px; padding: 12px;">
          <div class="muted" style="font-size: 12px; margin-bottom: 10px;">Эмодзи по статусу</div>
          <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 10px;">
            <div>
              <div class="muted" style="font-size: 12px; margin-bottom: 6px;">active</div>
              <InputText v-model="form.telegram_status_emoji_active" style="width: 100%;" />
            </div>
            <div>
              <div class="muted" style="font-size: 12px; margin-bottom: 6px;">waiting_manager</div>
              <InputText v-model="form.telegram_status_emoji_waiting_manager" style="width: 100%;" />
            </div>
            <div>
              <div class="muted" style="font-size: 12px; margin-bottom: 6px;">closed</div>
              <InputText v-model="form.telegram_status_emoji_closed" style="width: 100%;" />
            </div>
          </div>
        </div>

        <div class="panel" style="border-radius: 16px; padding: 12px;">
          <div class="muted" style="font-size: 12px; margin-bottom: 10px;">Эмодзи по событию</div>
          <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 10px;">
            <div>
              <div class="muted" style="font-size: 12px; margin-bottom: 6px;">Клиент</div>
              <InputText v-model="form.telegram_emoji_client" style="width: 100%;" />
            </div>
            <div>
              <div class="muted" style="font-size: 12px; margin-bottom: 6px;">Менеджер</div>
              <InputText v-model="form.telegram_emoji_manager" style="width: 100%;" />
            </div>
            <div>
              <div class="muted" style="font-size: 12px; margin-bottom: 6px;">AI</div>
              <InputText v-model="form.telegram_emoji_ai" style="width: 100%;" />
            </div>
            <div>
              <div class="muted" style="font-size: 12px; margin-bottom: 6px;">По умолчанию</div>
              <InputText v-model="form.telegram_emoji_default" style="width: 100%;" />
            </div>
          </div>
        </div>

        <div v-if="err" style="color:#ef4444; font-size: 13px;">{{ err }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import { useAuthStore } from '@/stores/auth'
import HelpTip from '@/components/HelpTip.vue'

const auth = useAuthStore()
const saving = ref(false)
const err = ref('')
const form = ref({
  telegram_group_mode: false,
  telegram_support_group_id: null as number | null,
  telegram_topic_title_template: '{emoji} {first_name} ({user_id}) {status_label}',
  telegram_emoji_default: '🟢',
  telegram_emoji_client: '🔴',
  telegram_emoji_manager: '🟡',
  telegram_emoji_ai: '🤖',
  telegram_status_emoji_active: '🟢',
  telegram_status_emoji_waiting_manager: '🟡',
  telegram_status_emoji_closed: '🔴'
})

const groupHelp =
  'Как получить ID группы:\n' +
  '1) Добавьте бота @userinfobot в группу и отправьте любое сообщение\n' +
  '2) Или временно включите логирование update в своём боте\n' +
  'ID группы обычно начинается с -100...'

const templateHelp =
  'Доступные переменные:\n' +
  '{emoji}, {status}, {status_label}, {first_name}, {last_name}, {username}, {user_id}, {chat_id}\n' +
  'Пример:\n' +
  '{emoji} {first_name} ({user_id}) {status_label}'

const groupIdStr = computed({
  get: () => (form.value.telegram_support_group_id == null ? '' : String(form.value.telegram_support_group_id)),
  set: (v: string) => {
    const t = v.trim()
    if (!t) {
      form.value.telegram_support_group_id = null
      return
    }
    const n = Number(t)
    form.value.telegram_support_group_id = Number.isFinite(n) ? Math.trunc(n) : null
  }
})

async function load() {
  err.value = ''
  const res = await fetch('/api/settings/telegram', { credentials: 'include' })
  if (!res.ok) {
    const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
    err.value = data.detail || 'Ошибка'
    return
  }
  const data = await res.json()
  form.value = { ...form.value, ...(data.effective || {}) }
}

async function save() {
  err.value = ''
  saving.value = true
  try {
    const payload = { ...form.value }
    const res = await fetch('/api/settings/telegram', {
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
