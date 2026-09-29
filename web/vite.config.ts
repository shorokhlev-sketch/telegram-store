import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// VITE_BASE: '/' in prod, a sub-path such as '/shop/' on a shared staging host. The API lives under the same base.
// In dev, `/<base>api` is proxied to VITE_API_PROXY (default: a local backend on :8000).
export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, process.cwd(), ''), ...process.env }
  let base = env.VITE_BASE || '/'
  if (!base.startsWith('/')) base = `/${base}`
  if (!base.endsWith('/')) base = `${base}/`
  const target = env.VITE_API_PROXY || 'http://127.0.0.1:8000'
  const targetUrl = new URL(target)
  const targetPrefix = targetUrl.pathname.replace(/\/$/, '')

  return {
    base,
    plugins: [react()],
    server: {
      proxy: {
        [`${base}api`]: {
          target: targetUrl.origin,
          changeOrigin: true,
          secure: targetUrl.protocol === 'https:',
          rewrite: (path) => targetPrefix + path.slice(base.length - 1),
        },
      },
    },
    build: { target: 'es2020', sourcemap: false },
  }
})
