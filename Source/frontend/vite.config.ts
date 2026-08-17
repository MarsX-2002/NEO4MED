import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // В разработке фронт и API на разных портах, а cookie сессии — httponly
    // с path=/. Прокси на тот же origin избавляет от CORS и от возни с
    // SameSite: браузер считает, что всё пришло с одного адреса.
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // Ручное разделение чанков не задаём: приложение маленькое, дефолтного
    // разбиения Vite достаточно. Файлы получают хэш в имени, поэтому nginx
    // кэширует /assets надолго, а index.html — нет.
  },
})
