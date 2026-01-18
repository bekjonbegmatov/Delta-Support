<template>
  <div>
    <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom: 12px;">
      <div>
        <div style="font-size: 18px; font-weight: 700;">Система</div>
        <div class="muted" style="font-size: 13px;">Кастомизация и параметры</div>
      </div>
      <Button v-if="auth.isAdmin" label="Добавить параметр" icon="pi pi-plus" @click="openAdd = true" />
    </div>

    <div class="panel card" style="padding: 10px; border-radius: 16px;">
      <DataTable :value="rows" responsiveLayout="scroll">
        <Column field="key" header="Ключ" />
        <Column field="value" header="Значение" />
        <Column header="Действия" style="width: 160px">
          <template #body="{ data }">
            <Button v-if="auth.isAdmin" size="small" text icon="pi pi-pencil" @click="edit(data)" />
          </template>
        </Column>
      </DataTable>
      <div v-if="err" style="color:#ef4444; font-size: 13px; padding: 10px;">{{ err }}</div>
    </div>

    <Dialog v-model:visible="openAdd" modal header="Параметр" :style="{ width: '520px' }">
      <div style="display:flex; flex-direction:column; gap:10px;">
        <InputText v-model="formKey" placeholder="Ключ (например site_name)" />
        <Textarea v-model="formValue" placeholder="Значение" autoResize rows="3" />
        <Textarea v-model="formDescription" placeholder="Описание" autoResize rows="2" />
        <Button label="Сохранить" icon="pi pi-check" :loading="saving" @click="save" />
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
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Textarea from 'primevue/textarea'

type Row = { key: string; value: string; description: string | null }

const auth = useAuthStore()
const rows = ref<Row[]>([])
const err = ref('')
const saving = ref(false)
const openAdd = ref(false)
const formKey = ref('')
const formValue = ref('')
const formDescription = ref('')

async function load() {
  err.value = ''
  const res = await fetch('/api/settings/system', { credentials: 'include' })
  if (!res.ok) {
    const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
    err.value = data.detail || 'Ошибка'
    return
  }
  rows.value = await res.json()
}

function edit(r: Row) {
  formKey.value = r.key
  formValue.value = r.value
  formDescription.value = r.description || ''
  openAdd.value = true
}

async function save() {
  err.value = ''
  saving.value = true
  try {
    if (!formKey.value.trim()) throw new Error('Ключ обязателен')
    const key = formKey.value.trim()
    const res = await fetch(`/api/settings/system/${encodeURIComponent(key)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ value: formValue.value, description: formDescription.value })
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
      throw new Error(data.detail || 'Ошибка')
    }
    openAdd.value = false
    formKey.value = ''
    formValue.value = ''
    formDescription.value = ''
    await load()
  } catch (e: any) {
    err.value = e?.message || 'Ошибка'
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

