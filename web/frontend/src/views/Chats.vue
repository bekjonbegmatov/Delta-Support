<template>
  <div class="chat-layout">
    <div class="panel card chat-list">
      <div class="chat-list-header">
        <div style="display:flex; flex-direction:column; gap:4px;">
          <div style="font-weight:800; font-size: 16px;">Чаты</div>
          <div class="muted" style="font-size: 12px;">{{ statusLabel }}</div>
        </div>
        <div class="btn-row">
          <Button text rounded icon="pi pi-refresh" @click="loadChats(true)" />
        </div>
      </div>

      <div class="chat-list-search">
        <span class="p-input-icon-left" style="width: 100%;">
          <i class="pi pi-search"></i>
          <InputText v-model="query" style="width: 100%;" placeholder="Поиск по имени/username..." />
        </span>
        <div class="chat-list-filters">
          <Button size="small" :severity="status==='active' ? 'success' : undefined" :outlined="status!=='active'" label="AI" @click="setStatus('active')" />
          <Button size="small" :severity="status==='waiting_manager' ? 'warning' : undefined" :outlined="status!=='waiting_manager'" label="Ожидают" @click="setStatus('waiting_manager')" />
          <Button size="small" :severity="status==='closed' ? 'secondary' : undefined" :outlined="status!=='closed'" label="Закрыт" @click="setStatus('closed')" />
          <Button size="small" :outlined="status!=='all'" label="Все" @click="setStatus('all')" />
        </div>
      </div>

      <div style="height: 100%; overflow: auto; min-height: 0;">
        <div
          v-for="item in chats"
          :key="item.id"
          class="chat-item"
          :class="{ active: item.id === activeId }"
          @click="openChat(item.id)"
        >
          <Avatar :label="avatarLabel(item)" shape="circle" />
          <div style="min-width: 0;">
            <div class="chat-item-title">{{ titleForChat(item) }}</div>
            <div class="chat-item-sub">
              <span v-if="item.last_message_at">последнее: {{ pretty(item.last_message_at) }}</span>
              <span v-else class="muted">нет сообщений</span>
            </div>
          </div>
          <Tag :severity="statusSeverity(item.status)" :value="statusName(item.status)" />
        </div>
        <div v-if="chats.length === 0" class="scroll-hint" style="padding: 18px 0;">
          Нет чатов по выбранному фильтру
        </div>
      </div>
    </div>

    <div class="panel card chat-room">
      <div class="chat-room-header">
        <div style="display:flex; align-items:center; gap:10px; min-width: 0;">
          <div v-if="activeChat" class="profile-trigger" @click="profileOpen = true">
            <img v-if="profile && !avatarFailed" :src="profile.avatar_url" @error="avatarFailed = true" />
            <Avatar v-else :label="avatarLabel(activeChat)" shape="circle" />
          </div>
          <div style="min-width: 0;">
            <div style="display:flex; align-items:center; gap:10px; flex-wrap: wrap;">
              <div style="font-weight:800; font-size: 15px;">
                {{ activeChat ? titleForChat(activeChat) : 'Выберите чат' }}
              </div>
              <Tag v-if="activeChat" :severity="statusSeverity(activeChat.status)" :value="statusName(activeChat.status)" />
            </div>
            <div v-if="activeChat" class="muted" style="font-size: 12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
              Telegram ID: {{ activeChat.user_id }} · Chat #{{ activeChat.id }}
            </div>
          </div>
        </div>

        <div class="btn-row" v-if="activeChat">
          <Button
            size="small"
            :icon="connectIcon"
            :label="connectLabel"
            :severity="activeChat.status === 'waiting_manager' ? 'secondary' : 'success'"
            @click="toggleConnect"
          />
        </div>
      </div>

      <div ref="messagesEl" class="chat-messages" @scroll="onScroll">
        <div v-if="activeChat && loadingMore" class="scroll-hint">Загрузка...</div>
        <div v-else-if="activeChat && hasMore" class="scroll-hint">Прокрутите вверх для истории</div>
        <div v-else-if="activeChat" class="scroll-hint">Начало истории</div>

        <template v-for="m in messages" :key="m.id">
          <div v-if="bubbleKind(m) === 'system'" class="bubble system">
            <div style="white-space: pre-wrap; text-align:center;">{{ m.text }}</div>
          </div>
          <div v-else class="bubble" :class="bubbleKind(m)">
            <div v-if="bubbleKind(m) === 'ai'" style="white-space: pre-wrap;" v-html="renderMarkdown(m.text)"></div>
            <div v-else>
              <template v-if="hasMedia(m)">
                <img v-if="effectiveMediaType(m) === 'photo'" class="media-img" :src="mediaUrl(m)" @click="lightboxUrl = mediaUrl(m)" />
                <video v-else-if="effectiveMediaType(m) === 'video'" class="media-video" controls :src="mediaUrl(m)"></video>
                <audio v-else-if="effectiveMediaType(m) === 'audio' || effectiveMediaType(m) === 'voice'" class="media-audio" controls :src="mediaUrl(m)"></audio>
                <div v-else-if="effectiveMediaType(m) === 'document'" class="media-doc">
                  <a :href="mediaUrl(m)" target="_blank" rel="noreferrer">{{ m.local_name || 'Скачать файл' }}</a>
                </div>
                <div v-else class="media-doc">
                  <a :href="mediaUrl(m)" target="_blank" rel="noreferrer">{{ m.local_name || 'Открыть медиа' }}</a>
                </div>
              </template>
              <div v-if="displayText(m)" style="white-space: pre-wrap; margin-top: 6px;">{{ displayText(m) }}</div>
            </div>
            <div class="bubble-meta">
              <span>{{ bubbleLabel(m) }}</span>
              <span v-if="m.created_at"> · {{ pretty(m.created_at) }}</span>
            </div>
          </div>
        </template>

        <div v-if="!activeChat" class="scroll-hint" style="padding-top: 20vh;">Выберите чат слева</div>
      </div>

      <div class="composer">
        <input ref="fileInput" type="file" style="display:none" @change="onFileChange" />
        <Button icon="pi pi-paperclip" text rounded :disabled="!activeId" @click="pickFile" />
        <Button :icon="recording ? 'pi pi-stop' : 'pi pi-microphone'" text rounded :disabled="!activeId" @click="toggleRecord" />
        <div class="composer-grow">
          <Textarea
            v-model="draft"
            autoResize
            rows="2"
            style="width: 100%;"
            placeholder="Введите сообщение… (Ctrl+Enter отправка)"
            @keydown="onComposerKeydown"
          />
          <div v-if="attachedFile" class="muted" style="font-size: 12px; margin-top: 6px; display:flex; align-items:center; gap:8px;">
            <i class="pi pi-paperclip"></i>
            <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">{{ attachedFile.name }}</span>
            <Button icon="pi pi-times" text rounded size="small" @click="clearFile" />
          </div>
          <div v-if="voicePreviewUrl" style="margin-top: 8px;">
            <audio :src="voicePreviewUrl" controls style="width: 100%;" />
          </div>
          <div v-if="micError" style="color:#ef4444; font-size: 12px; margin-top: 8px;">{{ micError }}</div>
        </div>
        <Button icon="pi pi-send" label="Отправить" :disabled="!activeId || (!draft.trim() && !attachedFile)" @click="send" />
      </div>
    </div>

    <div v-if="profileOpen && activeChat" class="sheet-overlay" @click="profileOpen = false">
      <div class="sheet" @click.stop>
        <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; padding: 14px;">
          <div style="display:flex; align-items:center; gap: 12px; min-width:0;">
            <div class="sheet-avatar">
              <img v-if="profile && !avatarFailed" :src="profile.avatar_url" @error="avatarFailed = true" />
              <span v-else>{{ avatarLabel(activeChat) }}</span>
            </div>
            <div style="min-width:0;">
              <div style="font-weight: 900; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
                {{ titleForChat(activeChat) }}
              </div>
              <div class="muted" style="font-size: 12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
                {{ activeChat.username ? '@' + activeChat.username : 'без username' }}
              </div>
            </div>
          </div>
          <Button icon="pi pi-times" text rounded @click="profileOpen = false" />
        </div>

        <div style="padding: 0 14px 14px;">
          <div class="sheet-grid">
            <div class="sheet-row"><span class="muted">Telegram ID</span><span>{{ activeChat.user_id }}</span></div>
            <div class="sheet-row"><span class="muted">Chat ID</span><span>{{ activeChat.id }}</span></div>
            <div class="sheet-row"><span class="muted">Статус</span><span>{{ statusName(activeChat.status) }}</span></div>
            <div class="sheet-row"><span class="muted">Назначен</span><span>{{ activeChat.assigned_admin_id ?? '—' }}</span></div>
            <div class="sheet-row"><span class="muted">Топик</span><span>{{ (profile && profile.topic_id) ?? '—' }}</span></div>
            <div class="sheet-row"><span class="muted">Обновлён</span><span>{{ pretty(activeChat.updated_at) }}</span></div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="lightboxUrl" class="lightbox-overlay" @click="lightboxUrl = null">
      <img class="lightbox-img" :src="lightboxUrl" @click.stop />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import Textarea from 'primevue/textarea'
import Avatar from 'primevue/avatar'
import Tag from 'primevue/tag'
import { useAuthStore } from '@/stores/auth'

type Chat = {
  id: number
  user_id: number
  user_tg_id: number | null
  username: string | null
  first_name: string | null
  last_name: string | null
  status: 'active' | 'waiting_manager' | 'closed'
  manager_id: number | null
  assigned_admin_id: number | null
  last_message_at: string | null
  updated_at: string
}

type Msg = {
  id: number
  text: string
  source: string
  created_at: string | null
  media_type?: string | null
  media_file_id?: string | null
  local_url?: string | null
  local_name?: string | null
}

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const chats = ref<Chat[]>([])
const activeId = ref<number | null>(null)
const activeChat = computed(() => chats.value.find((c) => c.id === activeId.value) || null)

const status = ref<'active' | 'waiting_manager' | 'closed' | 'all'>('active')
const query = ref('')
const draft = ref('')
const attachedFile = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const profileOpen = ref(false)
const profile = ref<any | null>(null)
const avatarFailed = ref(false)
const micError = ref('')
const recording = ref(false)
const voicePreviewUrl = ref<string | null>(null)
const lightboxUrl = ref<string | null>(null)
const messages = ref<Msg[]>([])
const messagesEl = ref<HTMLElement | null>(null)

const hasMore = ref(true)
const loadingMore = ref(false)
let ws: WebSocket | null = null
let debounceTimer: number | null = null
let mediaRecorder: MediaRecorder | null = null
let mediaStream: MediaStream | null = null
let mediaChunks: BlobPart[] = []

const statusLabel = computed(() => {
  if (status.value === 'active') return 'Автоответчик (AI)'
  if (status.value === 'waiting_manager') return 'Ожидают оператора'
  if (status.value === 'closed') return 'Закрытые'
  return 'Все'
})

function statusName(s: string) {
  if (s === 'active') return 'AI'
  if (s === 'waiting_manager') return 'Ожидает'
  return 'Закрыт'
}

function statusSeverity(s: string) {
  if (s === 'active') return 'success'
  if (s === 'waiting_manager') return 'warning'
  return 'secondary'
}

function pretty(iso: string | null) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleString()
}

function titleForChat(c: Chat) {
  const name = [c.first_name, c.last_name].filter(Boolean).join(' ').trim()
  if (name) return name
  if (c.username) return '@' + c.username
  return 'ID ' + c.user_id
}

function avatarLabel(c: Chat) {
  const name = [c.first_name, c.last_name].filter(Boolean).join(' ').trim()
  if (name) return name.slice(0, 1).toUpperCase()
  if (c.username) return c.username.slice(0, 1).toUpperCase()
  return String(c.user_id).slice(0, 1)
}

function bubbleKind(m: Msg) {
  const s = (m.source || '').toLowerCase()
  if (s.includes('manager')) return 'manager'
  if (s === 'ai') return 'ai'
  if (s === 'system') return 'system'
  return 'user'
}

function bubbleLabel(m: Msg) {
  const k = bubbleKind(m)
  if (k === 'manager') return 'Менеджер'
  if (k === 'ai') return 'AI'
  if (k === 'system') return 'Система'
  return 'Клиент'
}

function escapeHtml(s: string) {
  return (s || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

function renderMarkdown(raw: string) {
  let s = escapeHtml(raw)
  s = s.replace(
    /```([\s\S]*?)```/g,
    (_m, code) =>
      `<pre style="margin:10px 0; padding:10px 12px; border-radius:12px; overflow:auto; border:1px solid rgba(148,163,184,0.22); background: rgba(148,163,184,0.10);"><code>${code}</code></pre>`
  )
  s = s.replace(
    /`([^`]+)`/g,
    (_m, code) =>
      `<code style="padding:2px 6px; border-radius:8px; border:1px solid rgba(148,163,184,0.22); background: rgba(148,163,184,0.10);">${code}</code>`
  )
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>')
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
  s = s.replace(/\n/g, '<br />')
  return s
}

function hasMedia(m: Msg) {
  return Boolean(effectiveMediaType(m) && (m.media_file_id || m.local_url || (m.text || '').trim().startsWith('[')))
}

function mediaUrl(m: Msg) {
  if (m.local_url) return m.local_url
  return `/api/chats/messages/${m.id}/media`
}

function displayText(m: Msg) {
  const t = (m.text || '').trim()
  if (!t) return ''
  return t.replace(/^\[(photo|video|audio|voice|document|video_note)\]\s*/i, '').trim()
}

function inferMediaTypeFromText(text: string) {
  const t = (text || '').trim().toLowerCase()
  if (t.startsWith('[photo]')) return 'photo'
  if (t.startsWith('[video]')) return 'video'
  if (t.startsWith('[audio]')) return 'audio'
  if (t.startsWith('[voice]')) return 'voice'
  if (t.startsWith('[document]')) return 'document'
  if (t.startsWith('[video_note]')) return 'video_note'
  return null
}

function effectiveMediaType(m: Msg) {
  return m.media_type || inferMediaTypeFromText(m.text || '')
}

async function loadChats(force = false) {
  if (!auth.me) return
  const qs = new URLSearchParams()
  if (status.value !== 'all') qs.set('status', status.value)
  if (query.value.trim()) qs.set('q', query.value.trim())
  const res = await fetch(`/api/chats?${qs.toString()}`, { credentials: 'include' })
  if (!res.ok) {
    if (res.status === 401) {
      router.push('/login')
    }
    return
  }
  const data = await res.json()
  chats.value = data
  if (force && activeId.value) {
    const exists = chats.value.some((c) => c.id === activeId.value)
    if (!exists) activeId.value = null
  }
}

async function loadMessages(chatId: number, opts?: { beforeId?: number; prepend?: boolean }) {
  const qs = new URLSearchParams()
  qs.set('limit', '40')
  if (opts?.beforeId) qs.set('before_id', String(opts.beforeId))
  const res = await fetch(`/api/chats/${chatId}/messages?${qs.toString()}`, { credentials: 'include' })
  if (!res.ok) return
  const data: Msg[] = await res.json()
  if (opts?.prepend) {
    if (data.length === 0) {
      hasMore.value = false
      return
    }
    const el = messagesEl.value
    const prev = el ? el.scrollHeight : 0
    messages.value = [...data, ...messages.value]
    await nextTick()
    if (el) el.scrollTop = el.scrollHeight - prev
  } else {
    messages.value = data
    hasMore.value = data.length >= 40
    await nextTick()
    scrollBottom()
  }
}

function scrollBottom() {
  const el = messagesEl.value
  if (!el) return
  el.scrollTop = el.scrollHeight
}

async function openChat(id: number) {
  if (route.params.id !== String(id)) {
    router.push(`/chats/${id}`)
  }
  activeId.value = id
  hasMore.value = true
  profileOpen.value = false
  avatarFailed.value = false
  profile.value = null
  try {
    const res = await fetch(`/api/chats/${id}/profile`, { credentials: 'include' })
    if (res.ok) {
      profile.value = await res.json()
    } else if (res.status === 404) {
      const r2 = await fetch(`/api/chats/${id}`, { credentials: 'include' })
      if (r2.ok) {
        const data = await r2.json()
        profile.value = { ...data, avatar_url: `/api/chats/${id}/avatar` }
      }
    }
  } catch {
  }
  await loadMessages(id)
}

function addMessage(m: Msg) {
  const existing = messages.value.find((x) => x.id === m.id)
  if (existing) {
    if (m.media_type && existing.media_type !== m.media_type) existing.media_type = m.media_type
    if (!existing.media_file_id && m.media_file_id) {
      existing.media_file_id = m.media_file_id
      if (existing.local_url) {
        try {
          URL.revokeObjectURL(existing.local_url)
        } catch {
        }
        existing.local_url = null
      }
    }
    if ((!existing.text || existing.text.startsWith('[')) && m.text) existing.text = m.text
    if (!existing.created_at && m.created_at) existing.created_at = m.created_at
    return
  }
  messages.value.push(m)
}

async function send() {
  if (!activeId.value) return
  const text = draft.value.trim()
  const file = attachedFile.value
  if (!text && !file) return
  draft.value = ''
  attachedFile.value = null
  if (file) {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('text', text)
    const res = await fetch(`/api/chats/${activeId.value}/send-media`, {
      method: 'POST',
      credentials: 'include',
      body: fd
    })
    if (!res.ok) return
    const data = await res.json().catch(() => null)
    const messageId = data?.message_id ?? Date.now()
    const now = new Date().toISOString()
    const kind = (file.type || '').startsWith('image/')
      ? 'photo'
      : (file.type || '').startsWith('video/')
        ? 'video'
        : (file.type || '').startsWith('audio/')
          ? 'audio'
          : 'document'
    const storedText = text ? `[${kind}] ${text}` : `[${kind}]`
    const localUrl = voicePreviewUrl.value || URL.createObjectURL(file)
    voicePreviewUrl.value = null
    const m = {
      id: messageId,
      text: storedText,
      source: 'manager_web',
      created_at: now,
      media_type: kind,
      media_file_id: null,
      local_url: localUrl,
      local_name: file.name
    }
    if (activeChat.value && activeChat.value.id === activeId.value) {
      activeChat.value.last_message_at = now
    }
    addMessage(m)
    await nextTick()
    scrollBottom()
    return
  }
  const res = await fetch(`/api/chats/${activeId.value}/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ text })
  })
  if (!res.ok) return
  const data = await res.json().catch(() => null)
  const messageId = data?.message_id ?? Date.now()
  const now = new Date().toISOString()
  const m = { id: messageId, text, source: 'manager_web', created_at: now }
  if (activeChat.value && activeChat.value.id === activeId.value) {
    activeChat.value.last_message_at = now
  }
  addMessage(m)
  await nextTick()
  scrollBottom()
}

async function joinActive() {
  if (!activeId.value) return
  await fetch(`/api/chats/${activeId.value}/join`, { method: 'POST', credentials: 'include' })
}

async function backToAi() {
  if (!activeId.value) return
  await fetch(`/api/chats/${activeId.value}/ai`, { method: 'POST', credentials: 'include' })
}

const connectLabel = computed(() => {
  if (!activeChat.value) return 'Подключиться'
  return activeChat.value.status === 'waiting_manager' ? 'Завершить' : 'Подключиться'
})

const connectIcon = computed(() => {
  if (!activeChat.value) return 'pi pi-user-plus'
  return activeChat.value.status === 'waiting_manager' ? 'pi pi-check' : 'pi pi-user-plus'
})

async function toggleConnect() {
  if (!activeChat.value) return
  if (activeChat.value.status === 'waiting_manager') {
    await backToAi()
  } else {
    await joinActive()
  }
}

function setStatus(s: typeof status.value) {
  status.value = s
}

async function onScroll() {
  const el = messagesEl.value
  if (!el || !activeId.value) return
  if (el.scrollTop > 80) return
  if (!hasMore.value || loadingMore.value) return
  const first = messages.value[0]
  if (!first) return
  loadingMore.value = true
  try {
    await loadMessages(activeId.value, { beforeId: first.id, prepend: true })
  } finally {
    loadingMore.value = false
  }
}

function onComposerKeydown(ev: KeyboardEvent) {
  if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) {
    ev.preventDefault()
    send()
  }
}

function pickFile() {
  fileInput.value?.click()
}

function onFileChange(ev: Event) {
  const input = ev.target as HTMLInputElement | null
  const file = input?.files?.[0] || null
  attachedFile.value = file
  micError.value = ''
  if (voicePreviewUrl.value) {
    URL.revokeObjectURL(voicePreviewUrl.value)
    voicePreviewUrl.value = null
  }
  if (input) input.value = ''
}

function clearFile() {
  attachedFile.value = null
  micError.value = ''
  if (voicePreviewUrl.value) {
    URL.revokeObjectURL(voicePreviewUrl.value)
    voicePreviewUrl.value = null
  }
}

async function toggleRecord() {
  micError.value = ''
  if (recording.value) {
    try {
      mediaRecorder?.stop()
    } catch {
    }
    return
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    mediaStream = stream
    mediaChunks = []
    mediaRecorder = new MediaRecorder(stream)
    mediaRecorder.ondataavailable = (e: BlobEvent) => {
      if (e.data && e.data.size > 0) mediaChunks.push(e.data)
    }
    mediaRecorder.onstop = () => {
      recording.value = false
      try {
        mediaStream?.getTracks().forEach((t) => t.stop())
      } catch {
      }
      mediaStream = null
      const blob = new Blob(mediaChunks, { type: mediaRecorder?.mimeType || 'audio/webm' })
      mediaChunks = []
      if (voicePreviewUrl.value) {
        URL.revokeObjectURL(voicePreviewUrl.value)
        voicePreviewUrl.value = null
      }
      voicePreviewUrl.value = URL.createObjectURL(blob)
      attachedFile.value = new File([blob], 'voice.webm', { type: blob.type })
      mediaRecorder = null
    }
    if (voicePreviewUrl.value) {
      URL.revokeObjectURL(voicePreviewUrl.value)
      voicePreviewUrl.value = null
    }
    attachedFile.value = null
    recording.value = true
    mediaRecorder.start()
  } catch (e: any) {
    recording.value = false
    try {
      mediaStream?.getTracks().forEach((t) => t.stop())
    } catch {
    }
    mediaStream = null
    micError.value = 'Нет доступа к микрофону'
  }
}

function setupWS() {
  ws?.close()
  ws = new WebSocket(location.origin.replace('http', 'ws') + '/ws')
  ws.onmessage = async (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.event === 'new_message') {
      const chatId = msg.data.chat_id
      const raw = msg.data.message || {}
      const now = new Date().toISOString()
      const m = {
        id: raw.id ?? Date.now(),
        text: raw.text ?? raw.content ?? '',
        source: raw.source ?? 'system',
        created_at: raw.created_at ?? now,
        media_type: raw.media_type ?? null,
        media_file_id: raw.media_file_id ?? null
      }
      const chat = chats.value.find((c) => c.id === chatId)
      if (chat) {
        chat.last_message_at = m.created_at
      } else {
        if (status.value === 'all' || status.value === 'waiting_manager') {
          loadChats(true)
        }
      }
      if (activeId.value === chatId) {
        addMessage(m)
        await nextTick()
        scrollBottom()
      }
    }
    if (msg.event === 'status_changed') {
      const chatId = msg.data.chat_id
      const nextStatus = msg.data.status
      const chat = chats.value.find((c) => c.id === chatId)
      if (chat) {
        chat.status = nextStatus
        if ('assigned_admin_id' in msg.data) chat.assigned_admin_id = msg.data.assigned_admin_id
      }
      if (status.value !== 'all') {
        loadChats(true)
      } else if (!chat) {
        loadChats(true)
      }
    }
  }
}

watch([query, status], () => {
  if (debounceTimer) window.clearTimeout(debounceTimer)
  debounceTimer = window.setTimeout(() => loadChats(), 250)
})

onMounted(async () => {
  await auth.fetchMe()
  if (!auth.me) {
    router.push('/login')
    return
  }
  await loadChats()
  const rid = typeof route.params.id === 'string' ? Number(route.params.id) : NaN
  if (rid && Number.isFinite(rid)) {
    await openChat(rid)
  }
  setupWS()
})

watch(
  () => route.params.id,
  async (id) => {
    if (!auth.me) return
    const rid = typeof id === 'string' ? Number(id) : NaN
    if (!rid || !Number.isFinite(rid)) {
      activeId.value = null
      messages.value = []
      return
    }
    if (activeId.value !== rid) {
      await openChat(rid)
    }
  }
)
</script>
