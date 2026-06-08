import { resolve } from 'node:path'
import process from 'node:process'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const rootEnv = loadEnv(mode, resolve(process.cwd(), '..'), ['VITE_', 'MAPBOX_TOKEN'])
  const mapboxToken = rootEnv.VITE_MAPBOX_TOKEN || rootEnv.MAPBOX_TOKEN || ''

  return {
    envDir: '..',
    define: {
      'import.meta.env.VITE_MAPBOX_TOKEN': JSON.stringify(mapboxToken),
    },
    plugins: [react()],
  }
})
