<template>
  <div class="space-y-6">
    <!-- Header -->
    <div class="panel-header">
      <div class="panel-title-section">
        <h1 class="panel-title">AI Провайдеры</h1>
        <p class="panel-subtitle">Управление подключениями к AI сервисам с балансировкой нагрузки</p>
      </div>
      <Button label="Добавить провайдера" icon="pi pi-plus" @click="openCreateModal" class="add-button" />
    </div>

    <!-- Quick Stats -->
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-icon" style="background: linear-gradient(135deg, var(--app-brand) 0%, color-mix(in srgb, var(--app-brand) 70%, #000));">
          <i class="pi pi-server"></i>
        </div>
        <div class="stat-content">
          <div class="stat-value">{{ providers.length }}</div>
          <div class="stat-label">Провайдеров</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon" style="background: linear-gradient(135deg, #10a37f 0%, #0d8a6a 100%);">
          <i class="pi pi-check-circle"></i>
        </div>
        <div class="stat-content">
          <div class="stat-value">{{ activeProviders }}</div>
          <div class="stat-label">Активных</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon" style="background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);">
          <i class="pi pi-key"></i>
        </div>
        <div class="stat-content">
          <div class="stat-value">{{ totalApiKeys }}</div>
          <div class="stat-label">API Ключей</div>
        </div>
      </div>
    </div>

    <!-- Empty State -->
    <div v-if="providers.length === 0" class="empty-state">
      <div class="empty-icon">
        <i class="pi pi-inbox"></i>
      </div>
      <h3>Нет настроенных провайдеров</h3>
      <p>Добавьте первого AI провайдера для начала работы</p>
      <Button label="Добавить провайдера" icon="pi pi-plus" @click="openCreateModal" />
    </div>

    <!-- Providers Grid -->
    <div v-else class="providers-grid">
      <div v-for="p in providers" :key="p.id" class="provider-card" :class="{ 'provider-inactive': !p.is_active }">
        <!-- Provider Header -->
        <div class="provider-header">
          <div class="provider-badge" :style="{ background: p.provider_info?.color || 'var(--app-brand)' }">
            {{ (p.provider_info?.name || p.api_type).charAt(0).toUpperCase() }}
          </div>
          <div class="provider-info">
            <h3 class="provider-name">{{ p.name }}</h3>
            <span class="provider-type">{{ p.provider_info?.name || p.api_type }}</span>
          </div>
          <div class="provider-status">
            <Tag :value="p.is_active ? 'Активен' : 'Отключен'" :severity="p.is_active ? 'success' : 'secondary'" />
          </div>
        </div>

        <!-- Provider Details -->
        <div class="provider-details">
          <div class="detail-row">
            <div class="detail-label">
              <i class="pi pi-key"></i>
              <span>API Ключ</span>
            </div>
            <div class="api-key-display">
              <span class="key-masked">{{ p.api_key || 'Не установлен' }}</span>
              <Button v-if="p.api_key" icon="pi pi-play" text rounded size="small" @click="testProviderKey(p)" v-tooltip.top="'Тест'" :loading="testingId === p.id" class="test-btn" />
            </div>
          </div>
          <div class="detail-row">
            <div class="detail-label">
              <i class="pi pi-microchip"></i>
              <span>Модель</span>
            </div>
            <div class="detail-value">{{ p.model_name }}</div>
          </div>
          <div class="detail-row">
            <div class="detail-label">
              <i class="pi pi-link"></i>
              <span>Endpoint</span>
            </div>
            <div class="detail-value url-value" :title="p.base_url">{{ p.base_url || 'Default' }}</div>
          </div>
          <div class="detail-row">
            <div class="detail-label">
              <i class="pi pi-sort-amount-up"></i>
              <span>Приоритет</span>
            </div>
            <div class="detail-value">{{ p.priority }}</div>
          </div>

          <div class="detail-row">
            <div class="detail-label">
              <i class="pi pi-clock"></i>
              <span>Лимит</span>
            </div>
            <div class="detail-value">{{ p.max_requests_per_minute }}/мин</div>
          </div>
        </div>

        <!-- Provider Actions -->
        <div class="provider-actions">
          <Button icon="pi pi-play" text rounded size="small" @click="testProviderKey(p)" v-tooltip.top="'Тест'" :loading="testingId === p.id" />
          <Button icon="pi pi-pencil" text rounded size="small" @click="editProvider(p)" v-tooltip.top="'Редактировать'" />
          <Button icon="pi pi-trash" text rounded severity="danger" size="small" @click="deleteProvider(p.id)" v-tooltip.top="'Удалить'" />
        </div>
      </div>
    </div>

    <!-- Modal for Create/Edit -->
    <Dialog v-model:visible="showModal" :header="isEditing ? 'Редактировать провайдера' : 'Новый AI провайдер'" modal :style="{ width: '640px' }" :contentStyle="{ padding: '20px' }" class="provider-modal">
      <div class="modal-content">
        <!-- Provider Type Selection -->
        <div class="form-section">
          <label class="section-label">Тип провайдера</label>
          <div class="provider-types-grid">
            <div v-for="(info, key) in providerTypes" :key="key"
                 class="provider-type-card"
                 :class="{ 'selected': form.api_type === key }"
                 :style="{ '--provider-color': info.color }"
                 @click="selectProviderType(key)">
              <div class="type-icon">
                {{ info.name.charAt(0).toUpperCase() }}
              </div>
              <div class="type-name">{{ info.name }}</div>
            </div>
          </div>
        </div>

        <!-- Basic Info -->
        <div class="form-section">
          <label class="section-label">Основная информация</label>
          <div class="form-grid">
            <div class="field">
              <label class="field-label">Название</label>
              <InputText v-model="form.name" style="width: 100%" placeholder="Например: Production OpenAI" />
            </div>
            <div class="field">
              <label class="field-label">Модель</label>
              <Dropdown v-model="form.model_name" :options="visibleModels" optionLabel="label" optionValue="id"
                        editable filter style="width: 100%" placeholder="Выберите модель или введите ID" :loading="modelsLoading" />
              <div v-if="supportsDiscovery" class="model-actions">
                <Button label="Обновить модели" icon="pi pi-refresh" text size="small" @click="fetchModels" :loading="modelsLoading" :disabled="!form.api_key && !currentId" />
                <Button v-if="models.some(model => !model.likely_chat)" :label="showAllModels ? 'Только диалоговые' : 'Все модели'" text size="small" @click="showAllModels = !showAllModels" />
              </div>
              <small v-if="modelsError" class="model-error">{{ modelsError }} Можно ввести ID вручную.</small>
              <small v-else-if="models.length" class="field-help">Список получен из API. Поддержку инструментов и изображений проверяйте для выбранной модели. Модель не меняется автоматически.</small>
              <small v-else class="field-help">Введите ключ: доступные модели загрузятся автоматически.</small>
            </div>
          </div>
        </div>

        <!-- API Keys -->
        <div class="form-section">
          <div class="section-header">
            <label class="section-label">API Ключи</label>
            <Button label="Добавить ключ" icon="pi pi-plus" text size="small" @click="addApiKey" />
          </div>
          <div class="api-keys-list">
            <div class="api-key-item">
              <label class="field-label">Основной ключ</label>
              <div class="key-input-wrapper">
                <Password v-model="form.api_key" :feedback="false" toggleMask placeholder="sk-..." />
                <Button v-if="form.api_key" icon="pi pi-play" text rounded size="small" @click="testApiKey(form.api_key, 0)" v-tooltip.top="'Тест ключа'" :loading="testingIndex === 0" :class="{ 'test-success': testResults[0] === true, 'test-error': testResults[0] === false }" />
              </div>
              <div v-if="testResults[0] !== undefined" class="test-result" :class="testResults[0] ? 'success' : 'error'">
                <i :class="testResults[0] ? 'pi pi-check-circle' : 'pi pi-times-circle'"></i>
                <span>{{ testMessages[0] }}</span>
              </div>
            </div>
            <div v-for="(_key, idx) in form.api_keys_array" :key="idx" class="api-key-item additional">
              <label class="field-label">Дополнительный ключ #{{ idx + 2 }}</label>
              <div class="key-input-wrapper">
                <Password v-model="form.api_keys_array[idx]" :feedback="false" toggleMask placeholder="sk-..." />
                <Button v-if="form.api_keys_array[idx]" icon="pi pi-play" text rounded size="small" @click="testApiKey(form.api_keys_array[idx], idx + 1)" v-tooltip.top="'Тест ключа'" :loading="testingIndex === idx + 1" :class="{ 'test-success': testResults[idx + 1] === true, 'test-error': testResults[idx + 1] === false }" />
                <Button icon="pi pi-times" text rounded severity="danger" size="small" @click="removeApiKey(idx)" />
              </div>
              <div v-if="testResults[idx + 1] !== undefined" class="test-result" :class="testResults[idx + 1] ? 'success' : 'error'">
                <i :class="testResults[idx + 1] ? 'pi pi-check-circle' : 'pi pi-times-circle'"></i>
                <span>{{ testMessages[idx + 1] }}</span>
              </div>
            </div>
          </div>
          <div class="keys-help">
            <i class="pi pi-info-circle"></i>
            <span>Дополнительные ключи используются для балансировки нагрузки и резервирования</span>
          </div>
        </div>

        <!-- Advanced Settings -->
        <div class="form-section">
          <div class="section-header">
            <label class="section-label">Дополнительные настройки</label>
            <Button :label="showAdvanced ? 'Скрыть' : 'Показать'" text size="small" @click="showAdvanced = !showAdvanced" />
          </div>
          <div v-if="showAdvanced" class="advanced-settings">
            <div class="field">
              <label class="field-label">Base URL</label>
              <InputText v-model="form.base_url" style="width: 100%" :placeholder="providerTypes[form.api_type]?.default_base_url || 'https://api.example.com/v1'" />
            </div>
            <div class="form-grid">
              <div class="field">
                <label class="field-label">Приоритет</label>
                <InputNumber v-model="form.priority" style="width: 100%" :min="1" :max="100" />
              </div>
              <div class="field">
                <label class="field-label">Лимит запросов/мин</label>
                <InputNumber v-model="form.max_requests_per_minute" style="width: 100%" :min="1" :max="1000" />
              </div>
            </div>
          </div>
        </div>

        <!-- Status Toggle -->
        <div class="form-section status-section">
          <div class="toggle-field">
            <div>
              <div class="field-label">Активен</div>
              <div class="field-help">Провайдер будет использоваться для ответов AI</div>
            </div>
            <InputSwitch v-model="form.is_active" />
          </div>
        </div>
      </div>

      <template #footer>
        <Button label="Отмена" text @click="showModal = false" />
        <Button :label="isEditing ? 'Сохранить изменения' : 'Создать провайдера'" :icon="loading ? 'pi pi-spin pi-spinner' : 'pi pi-check'" :disabled="loading || !isFormValid" @click="saveProvider" />
      </template>
    </Dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Password from 'primevue/password'
import Dialog from 'primevue/dialog'
import Tag from 'primevue/tag'
import InputNumber from 'primevue/inputnumber'
import InputSwitch from 'primevue/inputswitch'
import Dropdown from 'primevue/dropdown'

interface ProviderType {
  name: string
  default_base_url: string
  default_model: string
  icon: string
  color: string
  models?: { label: string; value: string; badge?: string }[]
}

interface Provider {
  id: number
  name: string
  api_type: string
  api_key?: string
  api_keys_count: number
  base_url: string
  model_name: string
  is_active: boolean
  priority: number
  max_requests_per_minute: number
  provider_info: ProviderType
}

const providers = ref<Provider[]>([])
const providerTypes = ref<Record<string, ProviderType>>({})
const showModal = ref(false)
const isEditing = ref(false)
const loading = ref(false)
const showAdvanced = ref(false)
const currentId = ref<number | null>(null)
const testingId = ref<number | null>(null)
const testingIndex = ref<number | null>(null)
const testResults = ref<Record<number, boolean | undefined>>({})
const testMessages = ref<Record<number, string>>({})

const form = ref({
  name: '',
  api_type: 'openai_v2',
  api_key: '',
  api_keys_array: [] as string[],
  base_url: '',
  model_name: '',
  is_active: true,
  priority: 10,
  max_requests_per_minute: 60
})

const activeProviders = computed(() => providers.value.filter(p => p.is_active).length)
const totalApiKeys = computed(() => providers.value.reduce((sum, p) => sum + 1 + p.api_keys_count, 0))
const isFormValid = computed(() => form.value.name.trim() && form.value.model_name.trim() && (isEditing.value || form.value.api_key.trim()))

const models = ref<{ id: string; likely_chat: boolean }[]>([])
const modelsLoading = ref(false)
const modelsError = ref('')
const showAllModels = ref(false)
const supportsDiscovery = computed(() => ['openai_v2', 'openai', 'groq', 'deepseek', 'mistral', 'openrouter'].includes(form.value.api_type))
const visibleModels = computed(() => models.value.filter(model => showAllModels.value || model.likely_chat || model.id === form.value.model_name)
  .map(model => ({ ...model, label: model.id + (model.likely_chat ? '' : ' · другая специализация') })))
let modelsRequest = 0
let modelsTimer: ReturnType<typeof setTimeout> | undefined

const fetchModels = async () => {
  if (!supportsDiscovery.value || (!form.value.api_key.trim() && !currentId.value)) return
  if (modelsTimer) clearTimeout(modelsTimer)
  const requestId = ++modelsRequest
  modelsLoading.value = true
  modelsError.value = ''
  try {
    const response = await fetch('/api/ai/providers/models', {
      method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider_id: currentId.value, api_type: form.value.api_type,
        api_key: form.value.api_key, base_url: form.value.base_url })
    })
    const data = await response.json()
    if (requestId !== modelsRequest) return
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось получить модели')
    models.value = data.models || []
    if (!models.value.length) modelsError.value = 'API вернул пустой список.'
  } catch (error) {
    if (requestId === modelsRequest) modelsError.value = error instanceof Error ? error.message : 'Не удалось получить модели'
  } finally {
    if (requestId === modelsRequest) modelsLoading.value = false
  }
}

watch(() => [showModal.value, form.value.api_type, form.value.api_key, form.value.base_url, currentId.value], () => {
  ++modelsRequest
  models.value = []
  modelsError.value = ''
  modelsLoading.value = false
  if (modelsTimer) clearTimeout(modelsTimer)
  if (showModal.value && supportsDiscovery.value && (form.value.api_key.trim() || currentId.value)) {
    modelsTimer = setTimeout(fetchModels, 800)
  }
})
onUnmounted(() => { ++modelsRequest; if (modelsTimer) clearTimeout(modelsTimer) })

const fetchProviders = async () => {
  const res = await fetch('/api/ai/providers', { credentials: 'include' })
  if (res.ok) {
    providers.value = await res.json()
  }
}

const fetchProviderTypes = async () => {
  const res = await fetch('/api/ai/providers/types', { credentials: 'include' })
  if (res.ok) {
    const data = await res.json()
    providerTypes.value = data
  }
}

const selectProviderType = (type: string) => {
  form.value.api_type = type
  const provider = providerTypes.value[type]
  if (provider) {
    form.value.base_url = provider.default_base_url
  }
}

const addApiKey = () => {
  form.value.api_keys_array.push('')
}

const removeApiKey = (index: number) => {
  form.value.api_keys_array.splice(index, 1)
  delete testResults.value[index + 1]
  delete testMessages.value[index + 1]
}

const testApiKey = async (apiKey: string, index: number) => {
  if (!apiKey || !apiKey.trim()) return
  
  testingIndex.value = index
  testResults.value[index] = undefined
  testMessages.value[index] = ''
  
  try {
    const res = await fetch('/api/ai/providers/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        api_type: form.value.api_type,
        api_key: apiKey,
        base_url: form.value.base_url || providerTypes.value[form.value.api_type]?.default_base_url,
        model_name: form.value.model_name || providerTypes.value[form.value.api_type]?.default_model
      })
    })
    
    const data = await res.json()
    testResults.value[index] = data.ok
    testMessages.value[index] = data.message || data.detail || 'Неизвестная ошибка'
  } catch (e) {
    testResults.value[index] = false
    testMessages.value[index] = 'Ошибка сети'
  } finally {
    testingIndex.value = null
  }
}

const testProviderKey = async (provider: Provider) => {
  testingId.value = provider.id
  
  try {
    const testRes = await fetch('/api/ai/providers/test', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ provider_id: provider.id })
    })

    const testData = await testRes.json()
    
    if (testData.ok) {
      alert(`✅ ${testData.message}`)
    } else {
      alert(`❌ ${testData.message || testData.detail}`)
    }
  } catch (e) {
    alert('❌ Ошибка сети')
  } finally {
    testingId.value = null
  }
}

const openCreateModal = () => {
  isEditing.value = false
  currentId.value = null
  showAdvanced.value = false
  showAllModels.value = false
  testResults.value = {}
  testMessages.value = {}
  form.value = {
    name: '',
    api_type: 'openai_v2',
    api_key: '',
    api_keys_array: [],
    base_url: '',
    model_name: '',
    is_active: true,
    priority: 10,
    max_requests_per_minute: 60
  }
  showModal.value = true
}

const editProvider = async (p: Provider) => {
  isEditing.value = true
  currentId.value = p.id
  showAdvanced.value = true
  showAllModels.value = false
  testResults.value = {}
  testMessages.value = {}
  
  // Получаем реальные данные провайдера с сервера
  try {
    const res = await fetch(`/api/ai/providers/${p.id}`, { credentials: 'include' })
    if (res.ok) {
      const data = await res.json()
      form.value = {
        name: data.name,
        api_type: data.api_type,
        api_key: data.api_key || '',
        api_keys_array: data.api_keys || [],
        base_url: data.base_url || '',
        model_name: data.model_name,
        is_active: data.is_active,
        priority: data.priority,
        max_requests_per_minute: data.max_requests_per_minute
      }
    }
  } catch (e) {
    console.error('Failed to fetch provider details:', e)
    // Fallback к маскированным данным
    form.value = {
      name: p.name,
      api_type: p.api_type,
      api_key: '',
      api_keys_array: [],
      base_url: p.base_url || '',
      model_name: p.model_name,
      is_active: p.is_active,
      priority: p.priority,
      max_requests_per_minute: p.max_requests_per_minute
    }
  }
  
  showModal.value = true
}

const saveProvider = async () => {
  if (!form.value.name || !form.value.model_name) return
  
  loading.value = true
  try {
    const payload: any = {
      name: form.value.name,
      api_type: form.value.api_type,
      model_name: form.value.model_name,
      is_active: form.value.is_active,
      priority: form.value.priority,
      max_requests_per_minute: form.value.max_requests_per_minute,
      base_url: form.value.base_url || null
    }
    
    if (form.value.api_key && form.value.api_key.trim()) {
      payload.api_key = form.value.api_key
    }
    
    const additionalKeys = form.value.api_keys_array.filter(k => k && k.trim())
    if (additionalKeys.length > 0) {
      payload.api_keys = additionalKeys
    }
    
    const url = isEditing.value ? `/api/ai/providers/${currentId.value}` : '/api/ai/providers'
    const res = await fetch(url, {
      method: isEditing.value ? 'PATCH' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(payload)
    })
    
    if (res.ok) {
      showModal.value = false
      await fetchProviders()
    } else {
      const data = await res.json()
      alert(data.detail || 'Ошибка сохранения')
    }
  } finally {
    loading.value = false
  }
}

const deleteProvider = async (id: number) => {
  if (!confirm('Вы уверены, что хотите удалить этого провайдера?')) return
  const res = await fetch(`/api/ai/providers/${id}`, { method: 'DELETE', credentials: 'include' })
  if (res.ok) await fetchProviders()
}

onMounted(async () => {
  await fetchProviderTypes()
  await fetchProviders()
})
</script>

<style scoped>
.model-actions { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 5px; }
.model-error { color: var(--red-500, #ef4444); display: block; }
.field-help { display: block; margin-top: 5px; }
.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 24px;
}

.panel-title {
  font-size: 24px;
  font-weight: 700;
  margin: 0 0 4px 0;
  color: var(--app-text);
}

.panel-subtitle {
  font-size: 14px;
  color: var(--app-muted);
  margin: 0;
}

.add-button {
  flex-shrink: 0;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px;
  background: var(--app-surface);
  border: 1px solid var(--app-border);
  border-radius: var(--app-radius);
}

.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-size: 20px;
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: var(--app-text);
}

.stat-label {
  font-size: 13px;
  color: var(--app-muted);
}

.empty-state {
  text-align: center;
  padding: 60px 20px;
  background: var(--app-surface);
  border: 2px dashed var(--app-border);
  border-radius: var(--app-radius);
}

.empty-icon {
  width: 80px;
  height: 80px;
  margin: 0 auto 20px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--app-brand) 10%, transparent);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 32px;
  color: var(--app-brand);
}

.empty-state h3 {
  margin: 0 0 8px 0;
  font-size: 18px;
  color: var(--app-text);
}

.empty-state p {
  margin: 0 0 20px 0;
  color: var(--app-muted);
}

.providers-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 20px;
}

.provider-card {
  background: var(--app-surface);
  border: 1px solid var(--app-border);
  border-radius: var(--app-radius);
  padding: 20px;
  transition: all 0.2s;
}

.provider-card:hover {
  border-color: var(--app-brand);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
}

.provider-inactive {
  opacity: 0.6;
}

.provider-header {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--app-border);
}

.provider-badge {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-size: 24px;
  font-weight: 700;
  flex-shrink: 0;
}

.provider-info {
  flex: 1;
  min-width: 0;
}

.provider-name {
  font-size: 16px;
  font-weight: 700;
  margin: 0 0 4px 0;
  color: var(--app-text);
}

.provider-type {
  font-size: 13px;
  color: var(--app-muted);
}

.provider-details {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 16px;
}

.detail-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
}

.detail-label {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--app-muted);
}

.detail-label i {
  font-size: 14px;
}

.detail-value {
  font-weight: 500;
  color: var(--app-text);
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.url-value {
  font-size: 12px;
  max-width: 180px;
}

.api-key-display {
  display: flex;
  align-items: center;
  gap: 8px;
}

.key-masked {
  font-family: monospace;
  font-size: 12px;
  color: var(--app-muted);
  background: rgba(148, 163, 184, 0.1);
  padding: 4px 8px;
  border-radius: 4px;
}

.api-key-display .test-btn {
  margin-left: 0;
}

.keys-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 24px;
  height: 24px;
  padding: 0 8px;
  background: var(--app-brand);
  color: white;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 600;
}

.provider-actions {
  display: flex;
  gap: 8px;
  padding-top: 16px;
  border-top: 1px solid var(--app-border);
}

.provider-actions :deep(.p-button) {
  margin-left: auto;
}

.modal-content {
  display: flex;
  flex-direction: column;
  gap: 24px;
  padding: 8px 0;
}

.form-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.section-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--app-text);
}

.provider-types-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 12px;
}

.provider-type-card {
  padding: 14px 10px;
  background: var(--app-surface);
  border: 2px solid var(--app-border);
  border-radius: var(--app-radius);
  cursor: pointer;
  transition: all 0.2s;
  text-align: center;
}

.provider-type-card:hover {
  border-color: var(--provider-color);
  background: color-mix(in srgb, var(--provider-color) 5%, transparent);
}

.provider-type-card.selected {
  border-color: var(--provider-color);
  background: color-mix(in srgb, var(--provider-color) 10%, transparent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--provider-color) 20%, transparent);
}

.type-icon {
  width: 40px;
  height: 40px;
  margin: 0 auto 8px;
  border-radius: 10px;
  background: var(--provider-color);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-size: 22px;
  font-weight: 700;
}

.type-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--app-text);
  margin-bottom: 4px;
}

.space-y-6 > * + * {
  margin-top: 1.5rem;
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--app-text);
}

.api-keys-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.api-key-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.api-key-item.additional {
  padding-top: 12px;
  border-top: 1px dashed var(--app-border);
}

.key-input-wrapper {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.key-input-wrapper :deep(.p-password) {
  flex: 1;
}

.key-input-wrapper :deep(.p-password-input) {
  width: 100%;
}

.test-result {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 12px;
}

.test-result.success {
  background: rgba(16, 163, 127, 0.1);
  color: #10a37f;
}

.test-result.error {
  background: rgba(239, 68, 68, 0.1);
  color: #ef4444;
}

.test-result i {
  font-size: 16px;
}

.keys-help {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  background: rgba(148, 163, 184, 0.08);
  border-radius: 8px;
  font-size: 12px;
  color: var(--app-muted);
}

.keys-help i {
  font-size: 14px;
}

.advanced-settings {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  background: rgba(148, 163, 184, 0.05);
  border-radius: 12px;
  border: 1px solid var(--app-border);
}

.status-section {
  padding-top: 12px;
  border-top: 1px solid var(--app-border);
}

.toggle-field {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.field-help {
  font-size: 11px;
  color: var(--app-muted);
  margin-top: 2px;
}

.test-success :deep(.p-button) {
  color: #10a37f !important;
}

.test-error :deep(.p-button) {
  color: #ef4444 !important;
}

.space-y-6 > * + * {
  margin-top: 1.5rem;
}
</style>

<style>
.provider-modal .p-dialog-mask {
  background-color: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(4px);
}

.provider-modal .p-dialog {
  background: var(--app-surface);
  border: 1px solid var(--app-border);
  box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
}

.provider-modal .p-dialog-header {
  background: var(--app-surface);
  border-bottom: 1px solid var(--app-border);
  padding: 16px 20px;
}

.provider-modal .p-dialog-content {
  background: var(--app-surface);
  padding: 20px;
}

.provider-modal .p-dialog-footer {
  background: var(--app-surface);
  border-top: 1px solid var(--app-border);
  padding: 16px 20px;
}
</style>
