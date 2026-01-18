<template>
  <div>
    <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom: 12px;">
      <div>
        <div style="font-size: 18px; font-weight: 700;">Пользователи</div>
        <div class="muted" style="font-size: 13px;">Менеджеры и администраторы</div>
      </div>
      <Button v-if="auth.isAdmin" label="Добавить" icon="pi pi-plus" @click="openCreate = true" />
    </div>

    <div class="panel card" style="padding: 10px; border-radius: 16px;">
      <DataTable :value="users" responsiveLayout="scroll">
        <Column field="id" header="ID" style="width: 80px" />
        <Column field="username" header="Username" />
        <Column field="role" header="Роль" style="width: 140px" />
        <Column field="last_login" header="Последний вход" style="width: 210px">
          <template #body="{ data }">
            <span class="muted">{{ data.last_login ? pretty(data.last_login) : '—' }}</span>
          </template>
        </Column>
        <Column header="Доступ" style="width: 160px">
          <template #body="{ data }">
            <span class="muted">
              {{ data.access_start_hour != null && data.access_end_hour != null ? `${pad2(data.access_start_hour)}:00–${pad2(data.access_end_hour)}:00` : '—' }}
            </span>
          </template>
        </Column>
        <Column field="is_active" header="Активен" style="width: 120px">
          <template #body="{ data }">
            <Tag :value="data.is_active ? 'Да' : 'Нет'" :severity="data.is_active ? 'success' : 'danger'" />
          </template>
        </Column>
        <Column header="Действия" style="width: 260px">
          <template #body="{ data }">
            <div class="btn-row">
              <Button v-if="auth.isAdmin" size="small" text icon="pi pi-user-edit" @click="openEdit(data)" />
              <Button v-if="auth.isAdmin && data.role !== 'admin'" size="small" text :icon="data.is_active ? 'pi pi-ban' : 'pi pi-check'" @click="toggleActive(data)" />
              <Button v-if="auth.isAdmin" size="small" text icon="pi pi-key" @click="openReset(data)" />
            </div>
          </template>
        </Column>
      </DataTable>
    </div>

    <Dialog v-model:visible="openCreate" modal header="Добавить пользователя" :style="{ width: '420px' }">
      <div style="display:flex; flex-direction:column; gap:10px;">
        <InputText v-model="createUsername" placeholder="Username" />
        <Password v-model="createPassword" placeholder="Пароль (мин 8)" :feedback="false" toggleMask />
        <Dropdown v-model="createRole" :options="roles" optionLabel="label" optionValue="value" placeholder="Роль" />
        <Button label="Создать" icon="pi pi-check" :loading="saving" @click="createUser" />
        <div v-if="err" style="color:#ef4444; font-size: 13px;">{{ err }}</div>
      </div>
    </Dialog>

    <Dialog v-model:visible="openResetDialog" modal header="Сброс пароля" :style="{ width: '420px' }">
      <div style="display:flex; flex-direction:column; gap:10px;">
        <div class="muted" style="font-size: 13px;">Пользователь: {{ resetTarget?.username }}</div>
        <Password v-model="resetPassword" placeholder="Новый пароль (мин 8)" :feedback="false" toggleMask />
        <Button label="Сохранить" icon="pi pi-check" :loading="saving" @click="reset" />
        <div v-if="err" style="color:#ef4444; font-size: 13px;">{{ err }}</div>
      </div>
    </Dialog>

    <Dialog v-model:visible="openEditDialog" modal header="Пользователь" :style="{ width: '640px' }">
      <div v-if="editTarget" style="display:flex; flex-direction:column; gap: 12px;">
        <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 12px;">
          <div style="display:flex; flex-direction:column; gap: 10px;">
            <div class="muted" style="font-size: 12px;">Username</div>
            <InputText v-model="editUsername" placeholder="Username" />
            <div class="muted" style="font-size: 12px;">Роль</div>
            <Dropdown v-model="editRole" :options="roles" optionLabel="label" optionValue="value" placeholder="Роль" />
            <div class="muted" style="font-size: 12px;">Окно доступа (для менеджеров)</div>
            <div style="display:flex; gap: 10px;">
              <Dropdown v-model="editStartHour" :disabled="editRole === 'admin'" :options="hours" optionLabel="label" optionValue="value" placeholder="С" style="width: 100%;" />
              <Dropdown v-model="editEndHour" :disabled="editRole === 'admin'" :options="hours" optionLabel="label" optionValue="value" placeholder="До" style="width: 100%;" />
            </div>
            <Button label="Сохранить" icon="pi pi-check" :loading="saving" @click="saveUser" />
          </div>

          <div class="panel" style="border-radius: 16px; padding: 12px;">
            <div class="muted" style="font-size: 12px; margin-bottom: 10px;">Активность (7 дней)</div>
            <div class="muted" style="font-size: 12px;">Последний вход: {{ editTarget.last_login ? pretty(editTarget.last_login) : '—' }}</div>
            <div class="muted" style="font-size: 12px; margin-top: 6px;">Сообщений: {{ stats?.messages_7d ?? '—' }}</div>
            <div v-if="stats" style="margin-top: 10px; display:grid; gap: 6px;">
              <div v-for="(v, h) in stats.by_hour" :key="h" style="display:grid; grid-template-columns: 44px 1fr 34px; gap: 8px; align-items:center;">
                <div class="muted" style="font-size: 11px;">{{ pad2(h) }}</div>
                <div style="height: 8px; border-radius: 999px; background: rgba(148,163,184,0.16); overflow:hidden;">
                  <div :style="{ width: barWidth(v), height: '100%', background: 'rgba(34,197,94,0.65)' }"></div>
                </div>
                <div class="muted" style="font-size: 11px; text-align:right;">{{ v }}</div>
              </div>
            </div>
          </div>
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
import Password from 'primevue/password'
import Dropdown from 'primevue/dropdown'

type UserRow = {
  id: number
  username: string
  role: 'admin' | 'manager'
  is_active: boolean
  last_login: string | null
  access_start_hour: number | null
  access_end_hour: number | null
}

const auth = useAuthStore()
const users = ref<UserRow[]>([])
const err = ref('')
const saving = ref(false)

const openCreate = ref(false)
const createUsername = ref('')
const createPassword = ref('')
const createRole = ref<'admin' | 'manager'>('manager')
const roles = [
  { label: 'Менеджер', value: 'manager' },
  { label: 'Администратор', value: 'admin' }
]

const openResetDialog = ref(false)
const resetTarget = ref<UserRow | null>(null)
const resetPassword = ref('')

const openEditDialog = ref(false)
const editTarget = ref<UserRow | null>(null)
const editUsername = ref('')
const editRole = ref<'admin' | 'manager'>('manager')
const editStartHour = ref<number | null>(null)
const editEndHour = ref<number | null>(null)
const stats = ref<{ messages_7d: number; by_hour: number[] } | null>(null)

const hours = [
  { label: 'Нет', value: null },
  ...Array.from({ length: 24 }, (_, h) => ({ label: `${pad2(h)}:00`, value: h }))
]

function pad2(n: number) {
  return String(n).padStart(2, '0')
}

function pretty(iso: string | null) {
  if (!iso) return ''
  return new Date(iso).toLocaleString()
}

function barWidth(v: number) {
  const max = Math.max(...(stats.value?.by_hour || [0]))
  if (!max) return '0%'
  return `${Math.round((v / max) * 100)}%`
}

async function load() {
  err.value = ''
  const res = await fetch('/api/users', { credentials: 'include' })
  if (!res.ok) {
    const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
    err.value = data.detail || 'Ошибка'
    return
  }
  users.value = await res.json()
}

async function createUser() {
  err.value = ''
  saving.value = true
  try {
    const res = await fetch('/api/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ username: createUsername.value, password: createPassword.value, role: createRole.value })
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
      throw new Error(data.detail || 'Ошибка')
    }
    openCreate.value = false
    createUsername.value = ''
    createPassword.value = ''
    createRole.value = 'manager'
    await load()
  } catch (e: any) {
    err.value = e?.message || 'Ошибка'
  } finally {
    saving.value = false
  }
}

async function toggleActive(u: UserRow) {
  if (u.role === 'admin') {
    err.value = 'Администратора нельзя отключить'
    return
  }
  err.value = ''
  saving.value = true
  try {
    const res = await fetch(`/api/users/${u.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ is_active: !u.is_active })
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

function openReset(u: UserRow) {
  resetTarget.value = u
  resetPassword.value = ''
  openResetDialog.value = true
}

async function openEdit(u: UserRow) {
  err.value = ''
  stats.value = null
  editTarget.value = u
  editUsername.value = u.username
  editRole.value = u.role
  editStartHour.value = u.access_start_hour
  editEndHour.value = u.access_end_hour
  openEditDialog.value = true
  try {
    const res = await fetch(`/api/users/${u.id}/stats`, { credentials: 'include' })
    if (res.ok) stats.value = await res.json()
  } catch {
  }
}

async function saveUser() {
  if (!editTarget.value) return
  err.value = ''
  saving.value = true
  try {
    const res = await fetch(`/api/users/${editTarget.value.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        username: editUsername.value,
        role: editRole.value,
        access_start_hour: editStartHour.value,
        access_end_hour: editEndHour.value
      })
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
      throw new Error(data.detail || 'Ошибка')
    }
    await load()
    const updated = users.value.find((x) => x.id === editTarget.value?.id) || null
    editTarget.value = updated
    openEditDialog.value = false
  } catch (e: any) {
    err.value = e?.message || 'Ошибка'
  } finally {
    saving.value = false
  }
}

async function reset() {
  if (!resetTarget.value) return
  err.value = ''
  saving.value = true
  try {
    const res = await fetch(`/api/users/${resetTarget.value.id}/reset-password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ new_password: resetPassword.value })
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
      throw new Error(data.detail || 'Ошибка')
    }
    openResetDialog.value = false
    await load()
  } catch (e: any) {
    err.value = e?.message || 'Ошибка'
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  load()
})
</script>
