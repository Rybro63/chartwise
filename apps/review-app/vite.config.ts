/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development, /api is proxied to the encounter service. In the container image, nginx does
// the same (see nginx.conf), so the app always talks to its own origin and needs no CORS.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': process.env.API_URL ?? 'http://localhost:8080',
    },
  },
  test: {
    environment: 'node',
  },
})
