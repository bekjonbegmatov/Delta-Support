<template>
  <span ref="anchor" style="display:inline-flex; align-items:center;">
    <i class="pi pi-info-circle" style="cursor: help;" @mouseenter="open" @mouseleave="close" @click="toggle" />
  </span>
  <teleport to="body">
    <div
      v-if="visible"
      :style="boxStyle"
      @mouseenter="hovering = true"
      @mouseleave="hovering = false; close()"
    >
      <div style="white-space: pre-wrap;">{{ text }}</div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'

const props = defineProps<{ text: string }>()

const anchor = ref<HTMLElement | null>(null)
const visible = ref(false)
const hovering = ref(false)
const pos = ref({ top: 0, left: 0, width: 0, height: 0 })

function recalc() {
  const el = anchor.value
  if (!el) return
  const r = el.getBoundingClientRect()
  pos.value = { top: r.top, left: r.left, width: r.width, height: r.height }
}

function open() {
  recalc()
  visible.value = true
}

function close() {
  if (hovering.value) return
  visible.value = false
}

function toggle() {
  if (visible.value) close()
  else open()
}

function onScroll() {
  if (visible.value) recalc()
}

window.addEventListener('scroll', onScroll, true)
window.addEventListener('resize', onScroll, true)

onBeforeUnmount(() => {
  window.removeEventListener('scroll', onScroll, true)
  window.removeEventListener('resize', onScroll, true)
})

const boxStyle = computed(() => {
  const padding = 10
  const maxWidth = 520
  const gap = 10
  const top = pos.value.top + pos.value.height + gap
  const left = Math.min(pos.value.left, window.innerWidth - maxWidth - padding)
  return {
    position: 'fixed',
    top: `${Math.max(8, Math.min(top, window.innerHeight - 8))}px`,
    left: `${Math.max(8, left)}px`,
    maxWidth: `${maxWidth}px`,
    zIndex: 9999,
    background: 'var(--app-panel)',
    border: '1px solid var(--app-border)',
    color: 'var(--app-text)',
    borderRadius: '12px',
    padding: '10px 12px',
    boxShadow: '0 10px 30px rgba(0,0,0,0.20)',
    fontSize: '12px',
    lineHeight: '1.4'
  } as Record<string, string | number>
})
</script>
