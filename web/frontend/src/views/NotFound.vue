<template>
  <div class="panel card" style="border-radius: 16px; padding: 18px; max-width: 720px;">
    <div style="font-size: 18px; font-weight: 800;">Страница не найдена</div>
    <div class="muted" style="margin-top: 6px; font-size: 13px;">
      Такой страницы нет. Проверьте адрес или вернитесь на главную.
    </div>
    <div style="margin-top: 14px;">
      <Button label="Перейти в чаты" icon="pi pi-comments" @click="goChats" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import Button from 'primevue/button'

const router = useRouter()

function goChats() {
  router.push('/chats')
}
</script>

