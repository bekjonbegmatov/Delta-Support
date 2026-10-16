<template>
  <div>
    <div style="margin-bottom: 12px;">
      <div style="font-size: 18px; font-weight: 700;">Интеграция и бэкап</div>
      <div class="muted" style="font-size: 13px;">Внешний канал данных клиента, разрешения AI/tools и экспорт настроек</div>
    </div>

    <div class="panel card" style="padding: 16px; border-radius: var(--app-radius); margin-bottom: 16px;">
      <div style="font-weight: 700; margin-bottom: 4px;">Активный канал</div>
      <div class="muted" style="font-size: 13px; margin-bottom: 12px;">
        Пока одновременно активен только один источник данных о клиенте.
      </div>

      <div class="channel-grid">
        <button
          v-for="item in channels"
          :key="item.id"
          type="button"
          class="channel-card"
          :class="{ active: form.active_channel === item.id }"
          @click="form.active_channel = item.id"
        >
          <div style="display:flex; align-items:center; justify-content:space-between; gap:8px;">
            <div style="font-weight:700;">{{ item.label }}</div>
            <Tag
              :severity="form.active_channel === item.id ? 'success' : (item.configured ? 'info' : 'secondary')"
              :value="form.active_channel === item.id ? 'Активен' : (item.configured ? 'Готов' : 'Не настроен')"
            />
          </div>
          <div class="muted" style="font-size: 12px; margin-top: 8px;">{{ item.description }}</div>
        </button>
      </div>
    </div>

    <div class="panel card" style="padding: 16px; border-radius: var(--app-radius); margin-bottom: 16px;">
      <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom: 10px;">
        <div>
          <div style="font-weight: 700;">Support API</div>
          <div class="muted" style="font-size: 13px;">Баланс, платежи, подписки и ключи с основного сервера</div>
        </div>
        <Tag :severity="form.active_channel === 'support_api' ? 'success' : 'secondary'" :value="form.active_channel === 'support_api' ? 'Активен' : 'Не активен'" />
      </div>

      <div class="form-grid">
        <div>
          <label class="f-label">URL сервера</label>
          <InputText v-model="form.support_api.url" placeholder="https://web.deltavpn.me" style="width:100%;" />
        </div>
        <div>
          <label class="f-label">TTL кеша данных (сек)</label>
          <InputText v-model="form.support_api.cache_ttl" type="number" style="width: 180px;" />
        </div>
      </div>

      <div style="margin-top: 12px;">
        <label class="f-label">
          Токен
          <span v-if="form.support_api.token_set" class="muted" style="font-weight:400;"> — установлен: {{ form.support_api.token_preview }}</span>
        </label>
        <div style="display:flex; gap:8px; align-items:center;">
          <InputText
            v-model="form.support_api.token"
            type="password"
            :placeholder="form.support_api.token_set ? 'Оставьте пустым, чтобы не менять' : 'sup_...'"
            style="width:100%;"
            autocomplete="new-password"
          />
          <Button
            v-if="form.support_api.token_set"
            icon="pi pi-trash"
            severity="danger"
            text
            rounded
            title="Удалить токен"
            @click="clearSupportToken"
          />
        </div>
      </div>
    </div>

    <div class="panel card" style="padding: 16px; border-radius: var(--app-radius); margin-bottom: 16px;">
      <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom: 10px;">
        <div>
          <div style="font-weight: 700;">Remnawave v3+</div>
          <div class="muted" style="font-size: 13px;">Подписки, ссылки и устройства напрямую из Remnawave</div>
        </div>
        <Tag :severity="form.active_channel === 'remnawave' ? 'success' : 'secondary'" :value="form.active_channel === 'remnawave' ? 'Активен' : 'Не активен'" />
      </div>

      <div class="form-grid">
        <div>
          <label class="f-label">URL панели</label>
          <InputText v-model="form.remnawave.url" placeholder="https://remna.example.com" style="width:100%;" />
        </div>
        <div>
          <label class="f-label">Тип авторизации</label>
          <Dropdown
            v-model="form.remnawave.auth_type"
            :options="authTypes"
            optionLabel="label"
            optionValue="value"
            style="width: 220px;"
          />
        </div>
        <div>
          <label class="f-label">TTL кеша данных (сек)</label>
          <InputText v-model="form.remnawave.cache_ttl" type="number" style="width: 180px;" />
        </div>
      </div>

      <div style="margin-top: 12px; display:flex; flex-direction:column; gap:12px;">
        <div v-if="form.remnawave.auth_type !== 'basic'">
          <label class="f-label">
            API key
            <span v-if="form.remnawave.api_key_set" class="muted" style="font-weight:400;"> — установлен: {{ form.remnawave.api_key_preview }}</span>
          </label>
          <div style="display:flex; gap:8px; align-items:center;">
            <InputText
              v-model="form.remnawave.api_key"
              type="password"
              :placeholder="form.remnawave.api_key_set ? 'Оставьте пустым, чтобы не менять' : 'rw_...'"
              style="width:100%;"
              autocomplete="new-password"
            />
            <Button
              v-if="form.remnawave.api_key_set"
              icon="pi pi-trash"
              severity="danger"
              text
              rounded
              title="Удалить API key"
              @click="clearRemnaSecret('api_key')"
            />
          </div>
        </div>

        <div v-if="form.remnawave.auth_type === 'basic'" class="form-grid">
          <div>
            <label class="f-label">Username</label>
            <InputText v-model="form.remnawave.username" placeholder="admin" style="width:100%;" />
          </div>
          <div>
            <label class="f-label">
              Password
              <span v-if="form.remnawave.password_set" class="muted" style="font-weight:400;"> — установлен</span>
            </label>
            <div style="display:flex; gap:8px; align-items:center;">
              <InputText
                v-model="form.remnawave.password"
                type="password"
                :placeholder="form.remnawave.password_set ? 'Оставьте пустым, чтобы не менять' : 'password'"
                style="width:100%;"
                autocomplete="new-password"
              />
              <Button
                v-if="form.remnawave.password_set"
                icon="pi pi-trash"
                severity="danger"
                text
                rounded
                title="Удалить пароль"
                @click="clearRemnaSecret('password')"
              />
            </div>
          </div>
        </div>

        <div v-if="form.remnawave.auth_type === 'caddy'">
          <label class="f-label">
            Caddy token
            <span v-if="form.remnawave.caddy_token_set" class="muted" style="font-weight:400;"> — установлен: {{ form.remnawave.caddy_token_preview }}</span>
          </label>
          <div style="display:flex; gap:8px; align-items:center;">
            <InputText
              v-model="form.remnawave.caddy_token"
              type="password"
              :placeholder="form.remnawave.caddy_token_set ? 'Оставьте пустым, чтобы не менять' : 'caddy token'"
              style="width:100%;"
              autocomplete="new-password"
            />
            <Button
              v-if="form.remnawave.caddy_token_set"
              icon="pi pi-trash"
              severity="danger"
              text
              rounded
              title="Удалить Caddy token"
              @click="clearRemnaSecret('caddy_token')"
            />
          </div>
        </div>

        <div>
          <label class="f-label">
            Secret key / cookie
            <span v-if="form.remnawave.secret_key_set" class="muted" style="font-weight:400;"> — установлен: {{ form.remnawave.secret_key_preview }}</span>
          </label>
          <div style="display:flex; gap:8px; align-items:center;">
            <InputText
              v-model="form.remnawave.secret_key"
              type="password"
              :placeholder="form.remnawave.secret_key_set ? 'Оставьте пустым, чтобы не менять' : 'cookie_name:cookie_value'"
              style="width:100%;"
              autocomplete="new-password"
            />
            <Button
              v-if="form.remnawave.secret_key_set"
              icon="pi pi-trash"
              severity="danger"
              text
              rounded
              title="Удалить secret key"
              @click="clearRemnaSecret('secret_key')"
            />
          </div>
        </div>
      </div>
    </div>

    <div class="panel card" style="padding: 16px; border-radius: var(--app-radius); margin-bottom: 16px;">
      <div style="font-weight: 700; margin-bottom: 4px;">Разрешения по каналам</div>
      <div class="muted" style="font-size: 13px; margin-bottom: 14px;">
        Общие правила выдачи данных задаются один раз, а AI tools включаются отдельно для каждого канала. При переключении источника используются только его тумблеры.
      </div>

      <div style="display:flex; flex-direction:column; gap:12px; margin-bottom: 18px;">
        <label class="toggle-row">
          <div>
            <div style="font-weight:600;">Передавать данные в AI-контекст</div>
            <div class="muted" style="font-size:12px;">AI увидит данные аккаунта клиента в системном контексте и сможет отвечать точнее.</div>
          </div>
          <InputSwitch v-model="form.permissions.provide_ai_context" />
        </label>

        <label class="toggle-row">
          <div>
            <div style="font-weight:600;">Показывать карточку менеджеру</div>
            <div class="muted" style="font-size:12px;">Карточка клиента будет добавляться в топик, уведомления и веб-панель.</div>
          </div>
          <InputSwitch v-model="form.permissions.provide_manager_card" />
        </label>
      </div>

      <div class="permission-grid">
        <div class="permission-card">
          <div style="display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:8px;">
            <div>
              <div style="font-weight:700;">Support API</div>
              <div class="muted" style="font-size:12px;">Баланс, платежи, подписки и действия с основного сервера.</div>
            </div>
            <Tag :severity="form.active_channel === 'support_api' ? 'success' : 'secondary'" :value="form.active_channel === 'support_api' ? 'Активен' : 'Ожидает'" />
          </div>

          <div style="display:flex; flex-direction:column; gap:10px; margin-bottom: 14px;">
            <label v-for="featureName in featuresForChannel('support_api')" :key="featureName" class="toggle-row">
              <div>
                <div style="font-weight:600;">{{ featureMeta[featureName]?.label || featureName }}</div>
                <div class="muted" style="font-size:12px;">{{ featureMeta[featureName]?.description }}</div>
              </div>
              <InputSwitch v-model="form.permissions.features[featureName]" />
            </label>
          </div>

          <div style="font-weight: 600; margin-bottom: 10px;">AI tools Support API</div>
          <div class="tools-list">
            <div v-for="tool in toolsForChannel('support_api')" :key="'support-' + tool.name" class="tool-row">
              <div style="min-width:0;">
                <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                  <span style="font-weight:600;">{{ tool.label }}</span>
                  <Tag v-if="tool.destructive" severity="danger" value="Подтверждение клиента" />
                </div>
                <div class="muted" style="font-size:12px; margin-top:4px;">{{ tool.description }}</div>
              </div>
              <InputSwitch
                v-model="form.permissions.tools.support_api[tool.name]"
                :disabled="isPaymentTool(tool.name) && !form.permissions.features.payments"
              />
            </div>
          </div>
        </div>

        <div class="permission-card">
          <div style="display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:8px;">
            <div>
              <div style="font-weight:700;">Remnawave v3+</div>
              <div class="muted" style="font-size:12px;">Подписки, subscription_url и устройства напрямую из Remnawave.</div>
            </div>
            <Tag :severity="form.active_channel === 'remnawave' ? 'success' : 'secondary'" :value="form.active_channel === 'remnawave' ? 'Активен' : 'Ожидает'" />
          </div>

          <div style="display:flex; flex-direction:column; gap:10px; margin-bottom: 14px;">
            <label v-for="featureName in featuresForChannel('remnawave')" :key="featureName" class="toggle-row">
              <div>
                <div style="font-weight:600;">{{ featureMeta[featureName]?.label || featureName }}</div>
                <div class="muted" style="font-size:12px;">{{ featureMeta[featureName]?.description }}</div>
              </div>
              <InputSwitch v-model="form.permissions.features[featureName]" />
            </label>
          </div>

          <div style="font-weight: 600; margin-bottom: 10px;">AI tools Remnawave</div>
          <div class="tools-list">
            <div v-for="tool in toolsForChannel('remnawave')" :key="'remna-' + tool.name" class="tool-row">
              <div style="min-width:0;">
                <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                  <span style="font-weight:600;">{{ tool.label }}</span>
                  <Tag v-if="tool.destructive" severity="danger" value="Подтверждение клиента" />
                </div>
                <div class="muted" style="font-size:12px; margin-top:4px;">{{ tool.description }}</div>
              </div>
              <InputSwitch v-model="form.permissions.tools.remnawave[tool.name]" />
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="panel card" style="padding: 16px; border-radius: var(--app-radius); margin-bottom: 16px;">
      <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom: 8px; flex-wrap:wrap;">
        <div>
          <div style="font-weight: 700;">Проверка подключения</div>
          <div class="muted" style="font-size: 13px;">Тестовый запрос по Telegram ID через активный канал.</div>
        </div>
        <Tag :severity="form.active_channel === 'none' ? 'secondary' : 'info'" :value="form.active_channel === 'none' ? 'Канал не выбран' : activeChannelLabel" />
      </div>

      <div style="display:flex; gap:8px; align-items:center; flex-wrap: wrap;">
        <InputText v-model="testUserId" placeholder="Telegram ID клиента" style="width: 220px;" />
        <Button label="Проверить" icon="pi pi-bolt" :disabled="form.active_channel === 'none'" :loading="testing" @click="runTest" />
        <Button label="Сохранить настройки" icon="pi pi-check" :loading="saving" @click="save" />
      </div>
      <div v-if="msg" :style="{ color: msgOk ? '#22c55e' : '#ef4444', fontSize: '13px', marginTop: '8px' }">{{ msg }}</div>
      <div v-if="testError" style="color:#ef4444; font-size: 13px; margin-top: 8px;">{{ testError }}</div>
      <div v-if="testResult" style="display:grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px;">
        <div>
          <div class="muted" style="font-size: 12px; margin-bottom: 4px;">Контекст для AI</div>
          <pre class="preview-pre">{{ testResult.ai_context }}</pre>
        </div>
        <div>
          <div class="muted" style="font-size: 12px; margin-bottom: 4px;">Карточка для менеджера</div>
          <pre class="preview-pre">{{ testResult.manager_card }}</pre>
        </div>
      </div>
    </div>

    <div class="panel card" style="padding: 16px; border-radius: var(--app-radius);">
      <div style="font-weight: 700; margin-bottom: 4px;">Экспорт / импорт настроек</div>
      <div class="muted" style="font-size: 13px; margin-bottom: 12px;">
        Один JSON-файл: системные настройки, промпты, база знаний, AI-провайдеры и интеграционные секреты.
      </div>

      <div style="display:flex; flex-direction:column; gap:12px; max-width: 640px;">
        <div style="display:flex; align-items:center; gap:10px;">
          <Checkbox v-model="includeSecrets" binary inputId="incsec" />
          <label for="incsec">Включить секреты (API-ключи, токены, пароли)</label>
        </div>
        <div>
          <Button label="Скачать экспорт" icon="pi pi-download" :loading="exporting" @click="doExport" />
        </div>

        <div style="border-top: 1px solid var(--app-border); padding-top: 14px;">
          <div style="font-weight: 600; margin-bottom: 8px;">Импорт</div>
          <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
            <input ref="fileInput" type="file" accept="application/json,.json" @change="onFile" />
            <Dropdown v-model="importMode" :options="importModes" optionLabel="label" optionValue="value" style="width: 260px;" />
            <Button label="Импортировать" icon="pi pi-upload" :disabled="!importData" :loading="importing" @click="doImport" />
          </div>
          <div class="muted" style="font-size: 12px; margin-top: 6px;">
            «Слияние» — обновляет и дополняет; «Замена» — база знаний и провайдеры будут полностью заменены содержимым файла.
          </div>
          <div v-if="importMsg" :style="{ color: importOk ? '#22c55e' : '#ef4444', fontSize: '13px', marginTop: '8px' }">{{ importMsg }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import InputSwitch from 'primevue/inputswitch'
import Checkbox from 'primevue/checkbox'
import Dropdown from 'primevue/dropdown'
import Tag from 'primevue/tag'

type ToolMeta = {
  name: string
  label: string
  description: string
  destructive: boolean
}

type FeatureMeta = {
  label: string
  description: string
  channels: string[]
}

const authTypes = [
  { label: 'API key', value: 'api_key' },
  { label: 'Basic', value: 'basic' },
  { label: 'Caddy', value: 'caddy' }
]

const channels = ref<any[]>([])
const toolMeta = ref<ToolMeta[]>([])
const featureMeta = ref<Record<string, FeatureMeta>>({})
const capabilities = ref<Record<string, Record<string, boolean>>>({})

const form = ref({
  active_channel: 'none',
  support_api: {
    url: '',
    cache_ttl: '120',
    token: '',
    token_set: false,
    token_preview: ''
  },
  remnawave: {
    url: '',
    auth_type: 'api_key',
    cache_ttl: '120',
    api_key: '',
    api_key_set: false,
    api_key_preview: '',
    secret_key: '',
    secret_key_set: false,
    secret_key_preview: '',
    username: '',
    password: '',
    password_set: false,
    caddy_token: '',
    caddy_token_set: false,
    caddy_token_preview: ''
  },
  permissions: {
    features: {
      payments: true,
      subscription_profile: true,
      subscription_url: true
    },
    provide_ai_context: true,
    provide_manager_card: true,
    tools: {
      support_api: {} as Record<string, boolean>,
      remnawave: {} as Record<string, boolean>
    }
  }
})

const saving = ref(false)
const msg = ref('')
const msgOk = ref(false)

const testUserId = ref('')
const testing = ref(false)
const testError = ref('')
const testResult = ref<{ ai_context: string; manager_card: string } | null>(null)

const includeSecrets = ref(true)
const exporting = ref(false)
const importMode = ref('merge')
const importModes = [
  { label: 'Слияние (merge)', value: 'merge' },
  { label: 'Полная замена (replace)', value: 'replace' }
]
const importData = ref<any>(null)
const importing = ref(false)
const importMsg = ref('')
const importOk = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const activeChannelLabel = computed(() => {
  const item = channels.value.find((x) => x.id === form.value.active_channel)
  return item?.label || 'Без интеграции'
})

function isToolSupportedForChannel(channel: string, name: string) {
  return Boolean(capabilities.value?.[channel]?.[name])
}

function toolsForChannel(channel: string) {
  return toolMeta.value.filter((tool) => isToolSupportedForChannel(channel, tool.name))
}

function isPaymentTool(name: string) {
  return ['get_payment_methods', 'create_payment', 'check_payment'].includes(name)
}

function featuresForChannel(channel: string) {
  const order = ['payments', 'subscription_url', 'subscription_profile']
  return Object.entries(featureMeta.value)
    .filter(([, meta]) => Array.isArray(meta.channels) && meta.channels.includes(channel))
    .map(([name]) => name)
    .sort((a, b) => {
      const aIndex = order.indexOf(a)
      const bIndex = order.indexOf(b)
      return (aIndex === -1 ? 999 : aIndex) - (bIndex === -1 ? 999 : bIndex)
    })
}

function normalizeTools(channel: 'support_api' | 'remnawave', source: any) {
  const result: Record<string, boolean> = {}
  for (const tool of toolMeta.value) {
    result[tool.name] = source?.[tool.name] !== false
  }
  return result
}

async function load() {
  const res = await fetch('/api/settings/integration', { credentials: 'include' })
  if (!res.ok) return
  const data = await res.json()
  channels.value = data.channels || []
  toolMeta.value = data.tool_meta || []
  featureMeta.value = data.feature_meta || {}
  capabilities.value = data.capabilities || {}
  form.value.active_channel = data.active_channel || 'none'
  form.value.support_api = {
    url: data.support_api?.url || '',
    cache_ttl: String(data.support_api?.cache_ttl ?? 120),
    token: '',
    token_set: !!data.support_api?.token_set,
    token_preview: data.support_api?.token_preview || ''
  }
  form.value.remnawave = {
    url: data.remnawave?.url || '',
    auth_type: data.remnawave?.auth_type || 'api_key',
    cache_ttl: String(data.remnawave?.cache_ttl ?? 120),
    api_key: '',
    api_key_set: !!data.remnawave?.api_key_set,
    api_key_preview: data.remnawave?.api_key_preview || '',
    secret_key: '',
    secret_key_set: !!data.remnawave?.secret_key_set,
    secret_key_preview: data.remnawave?.secret_key_preview || '',
    username: data.remnawave?.username || '',
    password: '',
    password_set: !!data.remnawave?.password_set,
    caddy_token: '',
    caddy_token_set: !!data.remnawave?.caddy_token_set,
    caddy_token_preview: data.remnawave?.caddy_token_preview || ''
  }
  const loadedFeatures = data.permissions?.features || {}
  form.value.permissions = {
    features: {
      payments: loadedFeatures.payments !== false,
      subscription_profile: loadedFeatures.subscription_profile ?? (data.permissions?.subscription_profile !== false),
      subscription_url: loadedFeatures.subscription_url ?? (data.permissions?.subscription_url !== false)
    },
    provide_ai_context: data.permissions?.provide_ai_context !== false,
    provide_manager_card: data.permissions?.provide_manager_card !== false,
    tools: {
      support_api: normalizeTools('support_api', data.permissions?.tools?.support_api || data.permissions?.tools),
      remnawave: normalizeTools('remnawave', data.permissions?.tools?.remnawave || data.permissions?.tools)
    }
  }
}

function buildPayload(extra: any = {}) {
  return {
    active_channel: form.value.active_channel,
    support_api: {
      url: form.value.support_api.url,
      cache_ttl: form.value.support_api.cache_ttl,
      ...(form.value.support_api.token.trim() ? { token: form.value.support_api.token.trim() } : {}),
      ...(extra.support_api || {})
    },
    remnawave: {
      url: form.value.remnawave.url,
      auth_type: form.value.remnawave.auth_type,
      cache_ttl: form.value.remnawave.cache_ttl,
      username: form.value.remnawave.username,
      ...(form.value.remnawave.api_key.trim() ? { api_key: form.value.remnawave.api_key.trim() } : {}),
      ...(form.value.remnawave.secret_key.trim() ? { secret_key: form.value.remnawave.secret_key.trim() } : {}),
      ...(form.value.remnawave.password.trim() ? { password: form.value.remnawave.password.trim() } : {}),
      ...(form.value.remnawave.caddy_token.trim() ? { caddy_token: form.value.remnawave.caddy_token.trim() } : {}),
      ...(extra.remnawave || {})
    },
    permissions: {
      features: form.value.permissions.features,
      provide_ai_context: form.value.permissions.provide_ai_context,
      provide_manager_card: form.value.permissions.provide_manager_card,
      tools: form.value.permissions.tools
    }
  }
}

async function save(extra: any = {}) {
  msg.value = ''
  saving.value = true
  try {
    const res = await fetch('/api/settings/integration', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(buildPayload(extra))
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({ detail: 'Ошибка' }))
      throw new Error(data.detail || 'Ошибка')
    }
    msgOk.value = true
    msg.value = 'Сохранено'
    await load()
  } catch (e: any) {
    msgOk.value = false
    msg.value = e?.message || 'Ошибка'
  } finally {
    saving.value = false
  }
}

async function clearSupportToken() {
  await save({ support_api: { clear_token: true } })
}

async function clearRemnaSecret(field: 'api_key' | 'secret_key' | 'password' | 'caddy_token') {
  await save({ remnawave: { [`clear_${field}`]: true } })
}

async function runTest() {
  testError.value = ''
  testResult.value = null
  if (!testUserId.value.trim()) {
    testError.value = 'Укажите Telegram ID'
    return
  }
  testing.value = true
  try {
    const res = await fetch('/api/settings/integration/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ user_id: testUserId.value.trim(), channel: form.value.active_channel })
    })
    const data = await res.json()
    if (!data.ok) {
      testError.value = `Ошибка: ${data.error || 'unknown'}${data.detail ? ' — ' + data.detail : ''}`
      return
    }
    testResult.value = { ai_context: data.ai_context, manager_card: data.manager_card }
  } catch (e: any) {
    testError.value = e?.message || 'Ошибка запроса'
  } finally {
    testing.value = false
  }
}

async function doExport() {
  exporting.value = true
  try {
    const res = await fetch(`/api/settings/export?include_secrets=${includeSecrets.value}`, { credentials: 'include' })
    if (!res.ok) return
    const data = await res.json()
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    const ts = new Date().toISOString().slice(0, 10)
    a.download = `delta-support-settings-${ts}.json`
    a.click()
    URL.revokeObjectURL(a.href)
  } finally {
    exporting.value = false
  }
}

function onFile(e: Event) {
  importMsg.value = ''
  importData.value = null
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    try {
      importData.value = JSON.parse(String(reader.result))
    } catch {
      importOk.value = false
      importMsg.value = 'Файл не является корректным JSON'
    }
  }
  reader.readAsText(file)
}

async function doImport() {
  if (!importData.value) return
  importing.value = true
  importMsg.value = ''
  try {
    const res = await fetch('/api/settings/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ data: importData.value, mode: importMode.value })
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok || !data.ok) {
      importOk.value = false
      importMsg.value = data.detail || 'Ошибка импорта'
      return
    }
    importOk.value = true
    const c = data.imported || {}
    importMsg.value = `Импортировано: настроек ${c.system_config ?? 0}, база знаний ${c.knowledge_base ?? 0}, провайдеров ${c.ai_providers ?? 0}`
    importData.value = null
    if (fileInput.value) fileInput.value.value = ''
    await load()
  } catch (e: any) {
    importOk.value = false
    importMsg.value = e?.message || 'Ошибка импорта'
  } finally {
    importing.value = false
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
.preview-pre {
  background: rgba(148, 163, 184, 0.08);
  border: 1px solid var(--app-border);
  border-radius: var(--app-radius);
  padding: 10px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 320px;
  overflow: auto;
  margin: 0;
}
.channel-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
}
.channel-card {
  border: 1px solid var(--app-border);
  border-radius: 8px;
  padding: 12px;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.channel-card.active {
  border-color: rgba(34, 197, 94, 0.55);
  background: rgba(34, 197, 94, 0.08);
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}
.permission-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 12px;
}
.permission-card {
  border: 1px solid var(--app-border);
  border-radius: 8px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.toggle-row,
.tool-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}
.tools-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
@media (max-width: 720px) {
  .toggle-row,
  .tool-row {
    align-items: flex-start;
  }
}
</style>
