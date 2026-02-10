import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/status': {
        target: 'http://localhost:5123',
        changeOrigin: true
      },
      '/metadata': {
        target: 'http://localhost:5123',
        changeOrigin: true
      },
      '/petname': {
        target: 'http://localhost:5123',
        changeOrigin: true
      },
      '/outputs': {
        target: 'http://localhost:5123',
        changeOrigin: true
      },
      '/ce': {
        target: 'http://localhost:5123',
        changeOrigin: true
      },
      '/health': {
        target: 'http://localhost:5123',
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: 'dist'
  }
})
