import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` proxies the API to a locally running server (port 8081).
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8081" } },
});
