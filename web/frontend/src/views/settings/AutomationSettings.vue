<template>
  <div>
    <div style="margin-bottom: 12px;">
      <div style="font-size: 18px; font-weight: 700;">Автоматизация</div>
      <div class="muted" style="font-size: 13px;">Хранение истории, автозакрытие и контроль запросов менеджера</div>
    </div>

    <!-- Автозакрытие -->
    <div class="panel card" style="padding: 16px; border-radius: var(--app-radius); margin-bottom: 16px;">
      <div style="font-weight: 700; margin-bottom: 4px;">Автозакрытие неактивных чатов</div>
      <div class="muted" style="font-size: 13px; margin-bottom: 12px;">
        Если клиент не отвечает после ответа бота — сначала напоминание, затем закрытие. Пока приглашён менеджер или AI отключён, напоминания и автозакрытие не отправляются.
      </div>
      <div style="display:flex; flex-direction:column; gap:12px; max-width: 640px;">
        <div style="display:flex; align-items:center; gap:10px;">
          <InputSwitch v-model="form.auto_close_enabled" />
          <span>Включить автозакрытие</span>
        </div>
        <div style="display:flex; gap:16px; flex-wrap:wrap;">
          <div>
            <label class="f-label">Напоминание через (минут тишины)</label>
            <InputText v-model="form.auto_close_reminder_minutes" type="number" style="width: 180px;" />
          </div>
          <div>
            <label class="f-label">Закрыть через (минут после напоминания)</label>
            <InputText v-model="form.auto_close_after_minutes" type="number" style="width: 180px;" />
          </div>
        </div>
        <div>
          <label class="f-label">Текст напоминания клиенту</label>
          <Textarea v-model="form.auto_close_reminder_text" autoResize rows="2" style="width:100%;" />
        </div>
        <div>
          <label class="f-label">Текст при автозакрытии</label>
          <Textarea v-model="form.auto_close_text" autoResize rows="2" style="width:100%;" />
        </div>
      </div>
    </div>

    <div class="panel card" style="padding: 16px; border-radius: var(--app-radius); margin-bottom: 16px;">
      <div style="font-weight: 700; margin-bottom: 4px;">Хранение чатов и топиков</div>
      <div class="muted" style="font-size: 13px; margin-bottom: 12px;">
        Новый вопрос продолжает тот же диалог, даже если он был закрыт. Через указанный срок без сообщений
        клиента, AI или менеджера история и Telegram-топик удаляются окончательно. Служебные уведомления срок не продлевают.
      </div>
      <label class="f-label">Дней без сообщений до удаления</label>
      <InputText v-model="form.chat_retention_days" type="number" min="0" max="36500" style="width: 180px;" />
      <div class="muted" style="font-size: 12px; margin-top: 6px;">По умолчанию 90 дней. 0 — хранить бессрочно. Проверка каждый час.</div>
    </div>

    <!-- SLA -->
    <div class="panel card" style="padding: 16px; border-radius: var(--app-radius); margin-bottom: 16px;">
      <div style="font-weight: 700; margin-bottom: 4px;">SLA: контроль запросов менеджера</div>
      <div class="muted" style="font-size: 13px; margin-bottom: 12px;">
        Если клиент запросил менеджера и никто не взял чат в работу — бот повторно уведомит группу и всех сотрудников.
      </div>
      <div style="display:flex; flex-direction:column; gap:12px; max-width: 640px;">
        <div style="display:flex; align-items:center; gap:10px;">
          <InputSwitch v-model="form.sla_ping_enabled" />
          <span>Включить повторный пинг</span>
        </div>
        <div>
          <label class="f-label">Пинг, если запрос ждёт дольше (минут)</label>
          <InputText v-model="form.sla_ping_minutes" type="number" style="width: 180px;" />
        </div>
      </div>
    </div>

    <!-- AI и медиа -->
    <div class="panel card" style="padding: 16px; border-radius: var(--app-radius); margin-bottom: 16px;">
      <div style="font-weight: 700; margin-bottom: 4px;">AI: голосовые, фото и отчёты</div>
      <div class="muted" style="font-size: 13px; margin-bottom: 12px;">
        Расшифровка голосовых через Whisper и разбор фото, скриншотов и файлов изображений моделью с поддержкой зрения, в том числе OpenAI v2.
      </div>
      <div style="display:flex; flex-direction:column; gap:12px; max-width: 640px;">
        <div style="display:flex; align-items:center; gap:10px;">
          <InputSwitch v-model="form.ai_voice_enabled" />
          <span>AI отвечает на голосовые сообщения (расшифровка Whisper)</span>
        </div>
        <div style="display:flex; align-items:center; gap:10px;">
          <InputSwitch v-model="form.ai_vision_enabled" />
          <span>AI разбирает фото и скриншоты ошибок</span>
        </div>
        <div style="display:flex; align-items:center; gap:10px;">
          <InputSwitch v-model="form.weekly_report_enabled" />
          <span>Еженедельный отчёт админам в Telegram (понедельник, ~09:00 МСК)</span>
        </div>
      </div>
    </div>

    <div style="display:flex; gap:8px; align-items:center;">
      <Button label="Сохранить" icon="pi pi-check" :loading="saving" @click="save" />
      <span v-if="msg" :style="{ color: msgOk ? '#22c55e' : '#ef4444', fontSize: '13px' }">{{ msg }}</span>
    </div>

    <div class="muted" style="font-size: 12px; margin-top: 14px;">
      Команды менеджера в топике клиента: <b>/info</b> — карточка клиента, <b>/summary</b> — AI-сводка диалога,
      <b>/ai off|on</b> — отключить/включить AI в чате, <b>/note &lt;текст&gt;</b> — внутренняя заметка,
      <b>/ban</b> и <b>/unban</b> — блокировка клиента, <b>/close</b> — завершить сессию менеджера.
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import InputSwitch from 'primevue/inputswitch'
import Textarea from 'primevue/textarea'

const form = ref({
  chat_retention_days: '90',
  auto_close_enabled: false,
  auto_close_reminder_minutes: '360',
  auto_close_after_minutes: '720',
  auto_close_reminder_text: '',
  auto_close_text: '',
  sla_ping_enabled: true,
  sla_ping_minutes: '15',
  ai_voice_enabled: true,
  ai_vision_enabled: true,
  weekly_report_enabled: true
})
const saving = ref(false)
const msg = ref('')
const msgOk = ref(false)

async function load() {
  const res = await fetch('/api/settings/automation', { credentials: 'include' })
  if (!res.ok) return
  const data = await res.json()
  const e = data.effective || {}
  form.value.chat_retention_days = String(e.chat_retention_days ?? 90)
  form.value.auto_close_enabled = !!e.auto_close_enabled
  form.value.auto_close_reminder_minutes = String(e.auto_close_reminder_minutes ?? 360)
  form.value.auto_close_after_minutes = String(e.auto_close_after_minutes ?? 720)
  form.value.auto_close_reminder_text = e.auto_close_reminder_text || ''
  form.value.auto_close_text = e.auto_close_text || ''
  form.value.sla_ping_enabled = !!e.sla_ping_enabled
  form.value.sla_ping_minutes = String(e.sla_ping_minutes ?? 15)
  form.value.ai_voice_enabled = e.ai_voice_enabled !== false
  form.value.ai_vision_enabled = e.ai_vision_enabled !== false
  form.value.weekly_report_enabled = e.weekly_report_enabled !== false
}

async function save() {
  msg.value = ''
  saving.value = true
  try {
    const res = await fetch('/api/settings/automation', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(form.value)
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
      throw new Error(data.detail || 'Ошибка')
    }
    msgOk.value = true
    msg.value = 'Сохранено — бот подхватит настройки в течение минуты'
  } catch (e: any) {
    msgOk.value = false
    msg.value = e?.message || 'Ошибка'
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.f-label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 4px;
}
</style>
