<template>
  <div>
    <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom: 12px;">
      <div>
        <div style="font-size: 18px; font-weight: 700;">База знаний</div>
        <div class="muted" style="font-size: 13px;">Информация, доступная AI</div>
      </div>
      <Button v-if="auth.isAdmin" label="Добавить" icon="pi pi-plus" @click="openCreate" />
    </div>

    <section v-if="auth.isAdmin" class="panel card suggestions-panel">
      <div class="suggestions-heading">
        <div>
          <div style="font-size: 16px; font-weight: 700;">Каких знаний не хватает</div>
          <div class="muted" style="font-size: 13px; margin-top: 4px;">AI собирает темы из обращений, когда не находит нужной информации. Проверьте решение перед добавлением статьи.</div>
        </div>
        <div class="btn-row">
          <Dropdown v-model="suggestionStatus" :options="suggestionFilters" optionLabel="label" optionValue="value" aria-label="Статус предложений" @change="loadSuggestions" />
          <Button text icon="pi pi-refresh" aria-label="Обновить предложения" :loading="suggestionsLoading" @click="loadSuggestions" />
        </div>
      </div>
      <div v-if="suggestionError" class="kb-error">{{ suggestionError }}</div>
      <div v-else-if="suggestionsLoading" class="muted" style="padding: 12px 0;">Загрузка предложений…</div>
      <div v-else-if="!suggestions.length" class="muted" style="padding: 12px 0;">
        {{ suggestionStatus === 'open' ? 'Новых предложений пока нет. Они появятся автоматически по мере обращений клиентов.' : 'Предложений с таким статусом нет.' }}
      </div>
      <article v-for="suggestion in suggestions" :key="suggestion.id" class="suggestion-card">
        <div class="suggestions-heading">
          <strong>{{ suggestion.topic }}</strong>
          <Tag :value="suggestionLabels[suggestion.status]" :severity="suggestion.status === 'open' ? 'warning' : suggestion.status === 'resolved' ? 'success' : 'secondary'" />
        </div>
        <p>{{ suggestion.reason }}</p>
        <blockquote>{{ suggestion.example_question }}</blockquote>
        <div class="suggestion-meta muted">
          <span>Обращений: {{ suggestion.mention_count }}</span>
          <span>Последнее: {{ pretty(suggestion.last_seen_at) }}</span>
          <RouterLink v-if="suggestion.chat_id" :to="`/chats/${suggestion.chat_id}`">Открыть чат</RouterLink>
        </div>
        <div class="btn-row" style="margin-top: 10px;">
          <Button v-if="suggestion.status === 'open'" size="small" :label="suggestion.entry_id ? 'Дополнить черновик' : 'Написать статью'" icon="pi pi-file-edit" @click="openSuggestion(suggestion)" />
          <Button v-if="suggestion.status === 'open'" size="small" label="Отклонить" text :loading="updatingSuggestionId === suggestion.id" @click="setSuggestionStatus(suggestion, 'dismissed')" />
          <Button v-else size="small" label="Вернуть в работу" text :loading="updatingSuggestionId === suggestion.id" @click="setSuggestionStatus(suggestion, 'open')" />
        </div>
      </article>
    </section>

    <div class="panel card" style="padding: 10px; border-radius: var(--app-radius);">
      <DataTable :value="rows" responsiveLayout="scroll">
        <Column field="title" header="Название" />
        <Column field="is_active" header="Активно" style="width: 120px">
          <template #body="{ data }">
            <Tag :value="data.is_active ? 'Да' : 'Нет'" :severity="data.is_active ? 'success' : 'secondary'" />
          </template>
        </Column>
        <Column field="updated_at" header="Обновлено" style="width: 190px">
          <template #body="{ data }">{{ pretty(data.updated_at) }}</template>
        </Column>
        <Column header="Действия" style="width: 170px">
          <template #body="{ data }">
            <div class="btn-row">
              <Button v-if="auth.isAdmin" size="small" text icon="pi pi-pencil" @click="openEdit(data)" />
              <Button v-if="auth.isAdmin" size="small" text severity="danger" icon="pi pi-trash" @click="remove(data)" />
            </div>
          </template>
        </Column>
      </DataTable>
      <div v-if="err" style="color:#ef4444; font-size: 13px; padding: 10px;">{{ err }}</div>
    </div>

    <Dialog v-model:visible="open" modal header="Запись базы знаний" :style="{ width: '720px', maxWidth: '95vw' }">
      <div style="display:flex; flex-direction:column; gap:10px;">
        <div v-if="selectedSuggestion" class="suggestion-context">
          <strong>Что нужно уточнить</strong>
          <p>{{ selectedSuggestion.reason }}</p>
          <div class="muted">Пример вопроса: {{ selectedSuggestion.example_question }}</div>
          <p class="muted">Добавьте проверенную инструкцию. Предложение будет закрыто после сохранения активной статьи.</p>
        </div>
        <InputText v-model="form.title" placeholder="Название" />
        <Textarea v-model="form.content" autoResize rows="10" placeholder="Контент (текст/Markdown)" />
        <div style="display:flex; align-items:center; gap:10px;">
          <Checkbox v-model="form.is_active" :binary="true" inputId="kb_active" />
          <label for="kb_active" class="muted" style="font-size: 13px;">Активно</label>
        </div>
        <div class="btn-row">
          <Button label="Сохранить" icon="pi pi-check" :loading="saving" @click="save" />
          <Button label="Отмена" outlined @click="open=false" />
        </div>
        <div v-if="err" style="color:#ef4444; font-size: 13px;">{{ err }}</div>
      </div>
    </Dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Textarea from 'primevue/textarea'
import Checkbox from 'primevue/checkbox'
import Dropdown from 'primevue/dropdown'

type Row = { id: number; title: string; content: string; is_active: boolean; updated_at: string }
type SuggestionStatus = 'open' | 'dismissed' | 'resolved'
type Suggestion = { id: number; topic: string; reason: string; example_question: string; status: SuggestionStatus; mention_count: number; chat_id: number | null; entry_id: number | null; last_seen_at: string }

const auth = useAuthStore()
const rows = ref<Row[]>([])
const err = ref('')
const open = ref(false)
const saving = ref(false)
const editingId = ref<number | null>(null)
const suggestions = ref<Suggestion[]>([])
const suggestionStatus = ref<SuggestionStatus | 'all'>('open')
const suggestionsLoading = ref(false)
const suggestionError = ref('')
const updatingSuggestionId = ref<number | null>(null)
const selectedSuggestion = ref<Suggestion | null>(null)
const suggestionLabels: Record<SuggestionStatus, string> = { open: 'Новая тема', dismissed: 'Отклонено', resolved: 'Закрыто' }
const suggestionFilters = [
  { label: 'Новые', value: 'open' },
  { label: 'Закрытые', value: 'resolved' },
  { label: 'Отклонённые', value: 'dismissed' },
  { label: 'Все', value: 'all' }
]
const form = ref<{ title: string; content: string; is_active: boolean }>({ title: '', content: '', is_active: true })

function pretty(iso: string) {
  const d = new Date(iso)
  return d.toLocaleString()
}

async function load() {
  err.value = ''
  try {
    const res = await fetch('/api/kb', { credentials: 'include' })
    if (!res.ok) {
      const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
      throw new Error(data.detail || 'Ошибка')
    }
    rows.value = await res.json()
  } catch (e: any) {
    err.value = e?.message || 'Не удалось загрузить базу знаний'
  }
}

async function loadSuggestions() {
  if (!auth.isAdmin) return
  suggestionsLoading.value = true
  suggestionError.value = ''
  try {
    const res = await fetch(`/api/kb/suggestions?status=${suggestionStatus.value}`, { credentials: 'include' })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.detail || 'Не удалось загрузить предложения')
    }
    suggestions.value = await res.json()
  } catch (e: any) {
    suggestionError.value = e?.message || 'Не удалось загрузить предложения'
  } finally {
    suggestionsLoading.value = false
  }
}

async function setSuggestionStatus(suggestion: Suggestion, status: SuggestionStatus) {
  updatingSuggestionId.value = suggestion.id
  suggestionError.value = ''
  try {
    const res = await fetch(`/api/kb/suggestions/${suggestion.id}`, {
      method: 'PATCH', credentials: 'include', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status })
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.detail || 'Не удалось обновить предложение')
    }
    await loadSuggestions()
  } catch (e: any) {
    suggestionError.value = e?.message || 'Ошибка'
  } finally {
    updatingSuggestionId.value = null
  }
}

function openSuggestion(suggestion: Suggestion) {
  const draft = rows.value.find(row => row.id === suggestion.entry_id)
  if (draft) openEdit(draft)
  else {
    openCreate()
    form.value.title = suggestion.topic
  }
  selectedSuggestion.value = suggestion
}

function openCreate() {
  err.value = ''
  selectedSuggestion.value = null
  editingId.value = null
  form.value = { title: '', content: '', is_active: true }
  open.value = true
}

function openEdit(r: Row) {
  err.value = ''
  selectedSuggestion.value = null
  editingId.value = r.id
  form.value = { title: r.title, content: r.content, is_active: r.is_active }
  open.value = true
}

async function save() {
  err.value = ''
  saving.value = true
  try {
    const payload = { ...form.value, ...(!editingId.value && selectedSuggestion.value ? { suggestion_id: selectedSuggestion.value.id } : {}) }
    const res = await fetch(editingId.value ? `/api/kb/${editingId.value}` : '/api/kb', {
      method: editingId.value ? 'PATCH' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(payload)
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
      throw new Error(data.detail || 'Ошибка')
    }
    open.value = false
    await Promise.all([load(), loadSuggestions()])
  } catch (e: any) {
    err.value = e?.message || 'Ошибка'
  } finally {
    saving.value = false
  }
}

async function remove(r: Row) {
  err.value = ''
  const res = await fetch(`/api/kb/${r.id}`, { method: 'DELETE', credentials: 'include' })
  if (!res.ok) {
    const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
    err.value = data.detail || 'Ошибка'
    return
  }
  await load()
}

onMounted(() => Promise.all([load(), loadSuggestions()]))
</script>

<style scoped>
.suggestions-panel { padding: 16px; margin-bottom: 16px; }
.suggestions-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.suggestion-card { padding: 14px 0; border-top: 1px solid var(--surface-border, #ddd); margin-top: 12px; overflow-wrap: anywhere; }
.suggestion-card p, .suggestion-context p { margin: 10px 0; font-size: 13px; line-height: 1.5; }
.suggestion-card blockquote { margin: 10px 0; padding: 8px 12px; border-left: 3px solid var(--primary-color, #10b981); font-size: 13px; }
.suggestion-meta { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; }
.suggestion-context { border: 1px solid var(--surface-border, #ddd); padding: 12px; border-radius: 8px; font-size: 13px; }
.kb-error { color: #ef4444; font-size: 13px; padding: 10px 0; }
</style>
