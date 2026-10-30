<template>
  <div class="account-panel">
    <div style="display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom: 10px;">
      <div style="font-weight:700; font-size: 13px;">💳 Аккаунт в сервисе</div>
      <Button icon="pi pi-refresh" text rounded size="small" :loading="loading" @click="load(true)" />
    </div>

    <div v-if="loading && !data" class="muted" style="font-size: 13px;">Загрузка...</div>

    <div v-else-if="error" class="muted" style="font-size: 13px;">
      <template v-if="error === 'not_configured'">Интеграционный канал не настроен (Настройки → Интеграция и бэкап)</template>
      <template v-else-if="error === 'not_found'">Клиент не найден в системе сервиса</template>
      <template v-else>Не удалось загрузить данные</template>
    </div>

    <div v-else-if="data">
      <div class="acc-row">
        <span class="muted">Канал</span>
        <span style="font-weight:600;">{{ channelLabel }}</span>
      </div>
      <div v-if="hasBalance" class="acc-row">
        <span class="muted">Баланс</span>
        <span style="font-weight:700;">{{ data.user?.balance ?? '—' }} ₽</span>
      </div>
      <div v-else class="acc-row">
        <span class="muted">Баланс / платежи</span>
        <span>недоступно в этом канале</span>
      </div>
      <div v-if="data.user?.blocked" class="acc-row" style="color:#ef4444;">
        <span>🚫 Аккаунт заблокирован</span>
      </div>
      <div v-if="hasTrialInfo" class="acc-row">
        <span class="muted">Триал доступен</span>
        <span>{{ data.user?.trial_available ? 'да' : 'нет' }}</span>
      </div>
      <div class="acc-row">
        <span class="muted">Регистрация</span>
        <span>{{ shortDate(data.user?.created) }}</span>
      </div>

      <div v-if="subs.length" class="acc-section-title">
        Подписки ({{ activeSubs.length }} активных из {{ subs.length }})
      </div>
      <div v-for="(s, index) in subs" :key="subscriptionKey(s, index)" class="acc-card" :class="{ inactive: !s.is_currently_active }">
        <div style="display:flex; align-items:center; justify-content:space-between; gap:8px;">
          <div style="display:flex; align-items:center; gap:4px; min-width:0;">
            <span style="font-weight:600; overflow-wrap:anywhere;">{{ subscriptionCode(s) || 'Код подписки отсутствует' }}</span>
            <Button v-if="subscriptionCode(s)" icon="pi pi-copy" text rounded size="small" aria-label="Скопировать код подписки" @click="copy(subscriptionCode(s))" />
          </div>
          <Tag :severity="s.is_currently_active ? 'success' : 'secondary'" :value="s.is_currently_active ? 'активна' : 'неактивна'" />
        </div>
        <div v-if="s.tarif" class="muted" style="font-size:12px; margin-top:4px;">Тариф: {{ s.tarif }}</div>
        <div v-if="subscriptionMeta(s)" class="muted" style="font-size:12px; margin-top:4px;">
          {{ subscriptionMeta(s) }}
        </div>
        <div v-if="s.subscription_url" class="acc-key-row">
          <code class="acc-key">{{ s.subscription_url }}</code>
          <Button icon="pi pi-copy" text rounded size="small" @click="copy(s.subscription_url)" />
        </div>
      </div>

      <div v-if="outline.length" class="acc-section-title">Outline-ключи ({{ outline.length }})</div>
      <div v-for="k in outline" :key="'o' + k.id" class="acc-card">
        <div style="font-weight:600;">{{ k.profile_name || 'Outline' }}</div>
        <div class="acc-key-row">
          <code class="acc-key">{{ k.key_id }}</code>
          <Button icon="pi pi-copy" text rounded size="small" @click="copy(String(k.key_id))" />
        </div>
      </div>

      <div v-if="xray.length" class="acc-section-title">Xray-ключи ({{ xray.length }})</div>
      <div v-for="k in xray" :key="'x' + k.id" class="acc-card">
        <div style="display:flex; align-items:center; justify-content:space-between; gap:8px;">
          <span style="font-weight:600;">{{ k.name || 'Xray' }}</span>
          <Tag :severity="k.is_valid ? 'success' : 'danger'" :value="k.is_valid ? 'работает' : 'не работает'" />
        </div>
        <div class="muted" style="font-size:12px; margin-top:4px;">сервер: {{ k.server || '—' }}</div>
      </div>

      <div v-if="txs.length" class="acc-section-title">Последние платежи</div>
      <div v-for="(t, i) in txs" :key="'t' + i" class="acc-row">
        <span>{{ t.method_label || t.method }} · {{ shortDateTime(t.created) }}</span>
        <span :style="{ color: t.status ? '#22c55e' : '#f59e0b' }">{{ t.summa }} ₽</span>
      </div>

    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import Button from 'primevue/button'
import Tag from 'primevue/tag'

const props = defineProps<{ chatId: number | null }>()

const data = ref<any | null>(null)
const error = ref('')
const loading = ref(false)
let accountRequest = 0

const subs = computed(() => data.value?.connections?.remna_subscriptions || [])
const activeSubs = computed(() => subs.value.filter((s: any) => s.is_currently_active))
const outline = computed(() => data.value?.connections?.outline_keys || [])
const xray = computed(() => data.value?.connections?.xray_keys || [])
const txs = computed(() => (data.value?.transactions || []).slice(0, 3))
const hasBalance = computed(() => data.value?.user?.balance !== null && data.value?.user?.balance !== undefined)
const hasTrialInfo = computed(() => data.value?.user?.trial_available !== null && data.value?.user?.trial_available !== undefined)
const channelLabel = computed(() => data.value?.channel === 'remnawave' ? 'Remnawave v3+' : 'Support API')

function subscriptionCode(subscription: any): string {
  const displayName = typeof subscription.display_name === 'string' ? subscription.display_name.trim() : ''
  const username = typeof subscription.username === 'string' ? subscription.username.trim() : ''
  return displayName || username
}

function subscriptionKey(subscription: any, index: number): string {
  if (subscription.id !== null && subscription.id !== undefined) return `id:${subscription.id}`
  if (subscription.username) return `username:${subscription.username}`
  return `unidentified:${index}`
}

function shortDate(iso?: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (d.getFullYear() > 2070) return 'бессрочно'
  return d.toLocaleDateString()
}

function shortDateTime(iso?: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

function traffic(s: any) {
  const used = Number(s.traffic_used_bytes || 0) / 1024 ** 3
  const limit = Number(s.traffic_limit_bytes || 0)
  const limitStr = limit ? (limit / 1024 ** 3).toFixed(1) + ' ГБ' : '∞'
  return `${used.toFixed(1)}/${limitStr}`
}

function subscriptionMeta(s: any) {
  const parts: string[] = []
  if (s.expire_at) parts.push(`до ${shortDate(s.expire_at)}`)
  if (s.traffic_used_bytes !== undefined || s.traffic_limit_bytes !== undefined) parts.push(traffic(s))
  if (s.device_limit) parts.push(`устройств: ${s.device_limit}`)
  if (s.status_label) parts.push(statusLabel(s.status_label))
  if (!parts.length && s.subscription_url) parts.push('доступна subscription URL')
  return parts.join(' · ')
}

function statusLabel(status?: string | null) {
  const value = String(status || '').toUpperCase()
  if (value === 'ACTIVE') return 'активна'
  if (value === 'DISABLED') return 'отключена'
  if (value === 'EXPIRED') return 'истекла'
  if (value === 'LIMITED') return 'ограничена'
  return value ? value.toLowerCase() : '—'
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
  }
}

async function load(force = false) {
  const requestId = ++accountRequest
  const chatId = props.chatId
  if (!chatId) {
    loading.value = false
    return
  }
  loading.value = true
  error.value = ''
  try {
    const res = await fetch(`/api/chats/${chatId}/account${force ? '?force=true' : ''}`, { credentials: 'include' })
    const d = await res.json()
    if (requestId !== accountRequest || props.chatId !== chatId) return
    if (res.ok && d.ok) {
      data.value = d
    } else {
      data.value = null
      error.value = d.error || 'unknown'
    }
  } catch {
    if (requestId === accountRequest) error.value = 'unknown'
  } finally {
    if (requestId === accountRequest) loading.value = false
  }
}

watch(() => props.chatId, () => {
  data.value = null
  error.value = ''
  load()
}, { immediate: true })
</script>

<style scoped>
.account-panel {
  padding: 12px 14px;
  border-top: 1px solid var(--app-border);
}
.acc-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
}
.acc-section-title {
  font-size: 12px;
  font-weight: 700;
  opacity: 0.7;
  margin: 12px 0 6px;
  text-transform: uppercase;
  letter-spacing: 0.02em;
}
.acc-card {
  border: 1px solid var(--app-border);
  border-radius: calc(var(--app-radius) * 0.6);
  padding: 8px 10px;
  margin-bottom: 6px;
  font-size: 13px;
}
.acc-card.inactive {
  opacity: 0.55;
}
.acc-key-row {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 4px;
}
.acc-key {
  font-size: 12px;
  background: rgba(148, 163, 184, 0.12);
  border-radius: 6px;
  padding: 2px 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 220px;
}
</style>
