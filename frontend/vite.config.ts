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
  // По каким именам машины можно открыть dev-сервер: по умолчанию vite
  // пускает только localhost и IP. ALLOWED_HOSTS=all -- любое имя (так в
  // docker compose: проверяющие открывают сайт и по имени сервера), иначе --
  // список имён через запятую.
  const allowedHosts =
    env.ALLOWED_HOSTS === 'all' ? true : env.ALLOWED_HOSTS ? env.ALLOWED_HOSTS.split(',').map((h) => h.trim()) : undefined
  return {
    plugins: [react(), tailwindcss()],
    build: {
      rolldownOptions: {
        output: {
          // Библиотеки -- отдельными чанками: меняются реже кода приложения и
          // остаются в кэше браузера между релизами.
          codeSplitting: {
            groups: [
              { name: 'three', test: /node_modules[\\/]three[\\/]/, priority: 3 },
              { name: 'react-three', test: /node_modules[\\/]@react-three[\\/]/, priority: 2 },
              { name: 'three-extras', test: /node_modules[\\/](three-stdlib|three-mesh-bvh|troika[^\\/]*|camera-controls|maath|meshline|stats-gl|@monogrid)[\\/]/, priority: 2 },
              { name: 'react', test: /node_modules[\\/](react|react-dom|scheduler|react-router|@reduxjs|react-redux)[\\/]/, priority: 1 },
              { name: 'vendor', test: /node_modules/ },
            ],
          },
        },
      },
      // Ядро three.js после минификации -- около 700 КБ и на части не
      // делится; порог по умолчанию (500 КБ) ругался бы на него всегда.
      chunkSizeWarningLimit: 800,
    },
    server: {
      allowedHosts,
      proxy: { '/api': proxy, '/docs': proxy, '/openapi.json': proxy },
    },
  }
})
