import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// https://vite.dev/config/
//
// API -- через этот же dev-сервер: /api и Swagger (/docs, /openapi.json)
// проксируются на бэкенд, браузер обращается к одному адресу, и CORS не
// нужен. В Docker бэкенд доступен только из сети контейнеров
// (API_PROXY_TARGET=http://backend:8000), локально -- http://localhost:8000.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // Пустой VITE_API_BASE -- относительные пути /api/... (переменную читает
  // сам Vite после этого файла).
  process.env.VITE_API_BASE ??= ''
  const proxy = {
    target: env.API_PROXY_TARGET || 'http://localhost:8000',
    // X-Forwarded-For: лимиты частоты на бэкенде считаются по IP браузера.
    xfwd: true,
    // Разбор пачки DWG с ожиданием очереди -- до получаса.
    timeout: 30 * 60 * 1000,
    proxyTimeout: 30 * 60 * 1000,
  }
  return {
    plugins: [react(), tailwindcss()],
    server: {
      proxy: { '/api': proxy, '/docs': proxy, '/openapi.json': proxy },
    },
  }
})
