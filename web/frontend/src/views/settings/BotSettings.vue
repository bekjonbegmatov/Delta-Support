<template>
  <div class="space-y-6">
    <!-- Header -->
    <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom: 12px;">
      <div>
        <div style="font-size: 18px; font-weight: 700;">Настройки бота</div>
        <div class="muted" style="font-size: 13px;">Проект, приветствие и управление AI</div>
      </div>
      <div class="btn-row">
        <Button v-if="auth.isAdmin" label="Сохранить" icon="pi pi-check" :loading="saving" @click="saveAll" />
      </div>
    </div>

    <div class="space-y-4">
      <!-- Project Section -->
      <div class="panel card" style="padding: var(--app-padding); border-radius: var(--app-radius);">
        <div style="display:flex; align-items:center; gap:8px; margin-bottom: 16px;">
          <i class="pi pi-info-circle" style="color: var(--app-brand)"></i>
          <span style="font-weight: 700;">Информация о проекте</span>
        </div>
        
        <div class="grid-form">
          <div class="field">
            <label class="muted label-small">Название проекта</label>
            <InputText v-model="form.project_name" style="width: 100%;" />
          </div>
          <div class="field full-width">
            <label class="muted label-small">Описание проекта</label>
            <Textarea v-model="form.project_description" rows="3" style="width: 100%;" />
          </div>
          <div class="field">
            <label class="muted label-small">Сайт</label>
            <InputText v-model="form.project_website" style="width: 100%;" />
          </div>
          <div class="field">
            <label class="muted label-small">Ссылка на бота</label>
            <InputText v-model="form.project_bot_link" style="width: 100%;" />
          </div>
        </div>
      </div>

      <!-- Welcome Message Section -->
      <div class="panel card" style="padding: var(--app-padding); border-radius: var(--app-radius);">
        <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom: 16px;">
          <div style="display:flex; align-items:center; gap:8px;">
            <i class="pi pi-comment" style="color: var(--app-brand)"></i>
            <span style="font-weight: 700;">Приветственное сообщение</span>
          </div>
          <HelpTip :text="welcomeHelp" />
        </div>
        <Textarea v-model="form.bot_welcome_message" rows="5" style="width: 100%;" placeholder="Текст приветствия..." />
      </div>

      <!-- Manager Reply Section -->
      <div class="panel card" style="padding: var(--app-padding); border-radius: var(--app-radius);">
        <div style="display:flex; align-items:center; gap:8px; margin-bottom: 16px;">
          <i class="pi pi-user" style="color: var(--app-brand)"></i>
          <span style="font-weight: 700;">Сообщения менеджера клиенту</span>
        </div>

        <div class="grid-form">
          <div class="field full-width">
            <label class="muted label-small">Текст перед сообщением менеджера</label>
            <InputText v-model="form.manager_reply_prefix" style="width: 100%;" placeholder="👨‍💼 Менеджер поддержки" />
          </div>

          <div class="field full-width">
            <label class="muted label-small" style="margin-bottom: 8px;">Режим показа</label>
            <div style="display:flex; flex-direction:column; gap:8px;">
              <label style="display:flex; align-items:flex-start; gap:10px; cursor:pointer; padding:10px 12px; border:1px solid var(--app-border); border-radius: var(--app-radius);">
                <input type="radio" value="combined" v-model="form.manager_reply_style" style="margin-top:3px;" />
                <span>
                  <b>Одним сообщением</b>
                  <div class="muted" style="font-size:12px;">Текст менеджера каждый раз приходит одним сообщением: префикс и ответ вместе</div>
                </span>
              </label>
              <label style="display:flex; align-items:flex-start; gap:10px; cursor:pointer; padding:10px 12px; border:1px solid var(--app-border); border-radius: var(--app-radius);">
                <input type="radio" value="session_header" v-model="form.manager_reply_style" style="margin-top:3px;" />
                <span>
                  <b>Заголовок один раз за сессию</b>
                  <div class="muted" style="font-size:12px;">Префикс отправляется одним сообщением при первом ответе, дальше все сообщения менеджера в этой сессии идут ответом (reply) на него</div>
                </span>
              </label>
            </div>
          </div>

          <div class="field full-width">
            <div style="display:flex; align-items:center; gap:10px; padding: 12px; border:1px solid var(--app-border); border-radius: var(--app-radius);">
              <Checkbox v-model="form.client_ack_enabled" :binary="true" inputId="client_ack" />
              <label for="client_ack" style="cursor: pointer;">
                <div style="font-weight:600;">Подтверждение клиенту «✅ Ваше сообщение отправлено...»</div>
                <div class="muted" style="font-size:12px;">Отправляется при каждом сообщении, пока чат ждёт менеджера. Если раздражает — выключите; при ответе менеджера подтверждение по возможности удаляется автоматически</div>
              </label>
            </div>
          </div>
        </div>
      </div>

      <!-- AI Global Section -->
      <div class="panel card" style="padding: var(--app-padding); border-radius: var(--app-radius);">
        <div style="display:flex; align-items:center; gap:8px; margin-bottom: 16px;">
          <i class="pi pi-bolt" style="color: var(--app-brand)"></i>
          <span style="font-weight: 700;">AI Поддержка</span>
        </div>

        <div class="grid-form">
          <div class="field full-width">
            <div style="display:flex; align-items:center; gap:10px; padding: 12px; background: color-mix(in srgb, var(--app-brand) 5%, transparent); border-radius: var(--app-radius); border: 1px solid color-mix(in srgb, var(--app-brand) 10%, transparent);">
              <Checkbox v-model="form.ai_support_enabled" :binary="true" inputId="ai_enabled" />
              <label for="ai_enabled" style="cursor: pointer; font-weight: 600;">AI поддержка включена</label>
            </div>
          </div>

          <fieldset class="field full-width" style="border:1px solid var(--app-border); border-radius:var(--app-radius); padding:12px; gap:10px;">
            <legend style="font-weight:600;">Тип ответа AI</legend>
            <label style="display:flex; align-items:center; gap:8px; cursor:pointer;">
              <input type="radio" name="ai_response_mode" value="standard" v-model="form.ai_response_mode" />
              Обычный — готовый ответ одним сообщением
            </label>
            <label style="display:flex; align-items:center; gap:8px; cursor:pointer;">
              <input type="radio" name="ai_response_mode" value="streaming" v-model="form.ai_response_mode" />
              Потоковый — текст появляется по мере генерации
            </label>
            <div class="muted" style="font-size:12px;">
              В личном чате Telegram ответ постепенно появляется, затем сохраняется целиком.
              Если AI-провайдер или Telegram не поддерживает потоковый ответ, придёт обычное сообщение.
              Вызовы инструментов и служебные данные клиенту не показываются.
            </div>
          </fieldset>

          <div class="field full-width">
            <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom: 8px;">
              <label class="muted label-small">Системный промпт (Инструкция для AI)</label>
              <HelpTip :text="promptHelp" />
            </div>
            <Textarea v-model="form.ai_system_prompt" rows="8" style="width: 100%; min-height: 200px;" />
          </div>

          <!-- Link to AI Providers Page -->
          <div class="field full-width" style="margin-top: 10px;">
            <RouterLink to="/settings/ai-providers" style="display:flex; align-items:center; justify-content:center; gap:8px; padding: 16px; background: color-mix(in srgb, var(--app-brand) 8%, transparent); border: 1px dashed var(--app-brand); border-radius: var(--app-radius); color: var(--app-brand); text-decoration:none; font-weight: 600;">
              <i class="pi pi-external-link"></i>
              Управление AI провайдерами
            </RouterLink>
          </div>
        </div>
      </div>

      <div v-if="err" style="color:#ef4444; font-size: 13px; padding: 10px; background: rgba(239, 68, 68, 0.05); border-radius: 10px; border: 1px solid rgba(239, 68, 68, 0.2);">
        {{ err }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Textarea from 'primevue/textarea'
import Checkbox from 'primevue/checkbox'
import { useAuthStore } from '@/stores/auth'
import HelpTip from '@/components/HelpTip.vue'

const auth = useAuthStore()
const saving = ref(false)
const err = ref('')

// Bot form
const form = ref({
  project_name: '',
  project_description: '',
  project_website: '',
  project_bot_link: '',
  bot_welcome_message: '',
  ai_system_prompt: '',
  ai_support_enabled: true,
  ai_response_mode: 'standard',
  manager_reply_prefix: '👨‍💼 Менеджер поддержки',
  manager_reply_style: 'combined',
  client_ack_enabled: true
})

const welcomeHelp = 'Переменные: {first_name}, {username}, {project_name}, {project_bot_link}'
const promptHelp = 'Переменные: {project_name}, {service_context}, {first_name}, {user_id}'

async function load() {
  err.value = ''
  try {
    const bRes = await fetch('/api/settings/bot', { credentials: 'include' })
    if (bRes.ok) {
      const bData = await bRes.json()
      const incoming = bData.effective || {}
      if (typeof incoming.ai_support_enabled === 'string') {
        incoming.ai_support_enabled = ['1', 'true', 'yes', 'y', 'on'].includes(incoming.ai_support_enabled.trim().toLowerCase())
      }
      form.value = { ...form.value, ...incoming }
    }
  } catch (e) {
    err.value = 'Ошибка загрузки данных'
  }
}

async function saveAll() {
  err.value = ''
  saving.value = true
  try {
    const res = await fetch('/api/settings/bot', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(form.value)
    })
    if (!res.ok) throw new Error('Ошибка сохранения настроек')
    await load()
  } catch (e: any) {
    err.value = e.message
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.grid-form { display: grid; grid-template-columns: 1fr 1fr; gap: var(--app-gap); }
.field { display: flex; flex-direction: column; }
.full-width { grid-column: 1 / -1; }
.label-small { font-size: 11px; font-weight: 700; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--app-muted); }

.providers-list { display: flex; flex-direction: column; gap: calc(var(--app-gap) * 0.5); }
.provider-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  background: rgba(148, 163, 184, 0.05);
  border: 1px solid var(--app-border);
  border-radius: var(--app-radius);
}
.p-off { opacity: 0.5; }
.p-name { font-weight: 700; font-size: 14px; display: flex; align-items: center; }
.p-meta { font-size: 11px; color: var(--app-muted); margin-top: 2px; }

.form-grid-modal { display: flex; flex-direction: column; gap: 14px; padding-top: 10px; }

.space-y-6 > * + * { margin-top: 1.5rem; }
.space-y-4 > * + * { margin-top: 1rem; }
</style>
