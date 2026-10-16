import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import UnoCSS from 'unocss/vite';
import { compression } from 'vite-plugin-compression2';
import { resolve } from 'path';

export default defineConfig({
  plugins: [
    vue(),
    UnoCSS(),
    // 1. Generate .gz pre-compressed files
    compression({
      algorithms: ['gzip'],
      deleteOriginalAssets: false,
      threshold: 1024,
    }),
    // 2. Generate .br (Brotli) pre-compressed files
    compression({
      algorithms: ['brotliCompress'],
      deleteOriginalAssets: false,
      threshold: 1024,
    }),
  ],
  build: {
    outDir: resolve(__dirname, '../static'),
    emptyOutDir: true,
    target: 'esnext',
    minify: 'esbuild',
    rollupOptions: {
      output: {
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash].[ext]',
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:18088',
        changeOrigin: true,
      },
    },
  },
});
