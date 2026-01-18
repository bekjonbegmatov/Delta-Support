import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'node:path'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': `http://localhost:${process.env.VITE_API_PORT || '8080'}`,
      '/ws': {
        target: `ws://localhost:${process.env.VITE_API_PORT || '8080'}`,
        ws: true
      }
    }
  },
  build: {
    outDir: path.resolve(__dirname, '../static/spa'),
    emptyOutDir: true
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  }
})
