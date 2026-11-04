import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

export default defineConfig({
  plugins: [svelte()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/media": "http://localhost:8000",
    },
  },
  build: {
    outDir: "dist",
    assetsInlineLimit: 0,
    rollupOptions: {
      output: {
        // Fonts keep stable names so index.html can preload them.
        // They change only when the font package version changes.
        assetFileNames: (info) =>
          info.name?.endsWith(".woff2") ? "assets/fonts/[name].[ext]" : "assets/[name]-[hash].[ext]",
      },
    },
  },
});
