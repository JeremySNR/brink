import { defineConfig } from "vite";

// `npm run dev` proxies the play API to `brink serve` (default port 8765; override with BRINK_API).
const api = process.env.BRINK_API || "http://127.0.0.1:8765";

export default defineConfig({
  base: "./",
  build: { target: "es2022", chunkSizeWarningLimit: 900 },
  server: { proxy: { "/api": { target: api, changeOrigin: true } } },
});
