import {defineConfig} from 'vite';

export default defineConfig({
  server: {
    watch: {usePolling: true},
    proxy: {
      '/api': {
        target: process.env.BACKEND_URL || 'http://backend:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/otlp': {
        target: process.env.OTEL_EXPORTER_OTLP_ENDPOINT || 'http://otel-collector:4318',
        rewrite: (path) => path.replace(/^\/otlp/, ''),
      },
    },
  },
});
