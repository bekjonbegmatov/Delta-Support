<template>
  <div>
    <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom: 12px;">
      <div>
        <div style="font-size: 18px; font-weight: 700;">База знаний</div>
        <div class="muted" style="font-size: 13px;">Информация, доступная AI</div>
      </div>
      <Button v-if="auth.isAdmin" label="Добавить" icon="pi pi-plus" @click="openCreate" />
    </div>

    <div class="panel card" style="padding: 10px; border-radius: 16px;">
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

    <Dialog v-model:visible="open" modal header="Запись базы знаний" :style="{ width: '720px' }">
      <div style="display:flex; flex-direction:column; gap:10px;">
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

type Row = { id: number; title: string; content: string; is_active: boolean; updated_at: string }

const auth = useAuthStore()
const rows = ref<Row[]>([])
const err = ref('')
const open = ref(false)
const saving = ref(false)
const editingId = ref<number | null>(null)
const form = ref<{ title: string; content: string; is_active: boolean }>({ title: '', content: '', is_active: true })

function pretty(iso: string) {
  const d = new Date(iso)
  return d.toLocaleString()
}

async function load() {
  err.value = ''
  const res = await fetch('/api/kb', { credentials: 'include' })
  if (!res.ok) {
    const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
    err.value = data.detail || 'Ошибка'
    return
  }
  rows.value = await res.json()
}

function openCreate() {
  editingId.value = null
  form.value = { title: '', content: '', is_active: true }
  open.value = true
}

function openEdit(r: Row) {
  editingId.value = r.id
  form.value = { title: r.title, content: r.content, is_active: r.is_active }
  open.value = true
}

async function save() {
  err.value = ''
  saving.value = true
  try {
    const payload = { ...form.value }
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
    await load()
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

onMounted(load)
</script>
