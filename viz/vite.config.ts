import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    // public/data is a symlink to the pipeline's export directory, so the
    // viewer always shows the most recent run without a copy step.
    fs: { allow: ['..', '../..'] },
  },
  build: { chunkSizeWarningLimit: 2000 },
})
