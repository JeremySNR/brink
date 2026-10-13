import { defineConfig } from "vite";

// `npm run dev` proxies /api to `brink serve` (default port 8765) or, for hosted multiplayer, to the hosted
// API (`BRINK_API=http://127.0.0.1:8799 npm run dev`, see docs/HOSTING.md).
const api = process.env.BRINK_API || "http://127.0.0.1:8765";

export default defineConfig({
  base: "./",
  // the Supabase project URL and anon key (public by design) come from the Vercel/Supabase integration's
  // NEXT_PUBLIC_ variables, or VITE_ ones locally; nothing else from the environment reaches the bundle
  envPrefix: ["VITE_", "NEXT_PUBLIC_"],
  build: { target: "es2022", chunkSizeWarningLimit: 900 },
  server: { proxy: { "/api": { target: api, changeOrigin: true } } },
});
