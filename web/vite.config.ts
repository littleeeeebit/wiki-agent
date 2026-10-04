import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  // Open windows may still import content-hashed chunks from the previous build.
  // ponytail: retain old assets; prune offline if build storage becomes material.
  build: { emptyOutDir: false },
  // 개발 중에는 vite 가 화면을, 파이썬이 API 를 든다.
  server: { proxy: { '/api': 'http://127.0.0.1:8787' } },
})
