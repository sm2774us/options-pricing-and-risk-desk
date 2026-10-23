import react from '@vitejs/plugin-react';
import {defineConfig} from 'vitest/config';

const gateway = process.env.OPTDESK_GATEWAY ?? 'http://localhost:8000';
const proxy = {
  '/ws': {target: gateway, ws: true, changeOrigin: true},
  '/api': {target: gateway, changeOrigin: true},
};

export default defineConfig({
  plugins: [react()],
  server: {port: 5173, proxy},
  preview: {port: 4173, proxy},
  test: {environment: 'node', include: ['src/**/*.test.ts']},
});
