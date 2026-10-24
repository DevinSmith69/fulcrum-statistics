import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, strictPort: true },
  // One ~190 kB (gzip) bundle is fine for a single-page overlay; recharts is most of it.
  build: { chunkSizeWarningLimit: 900 },
});
