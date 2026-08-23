import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiHost = process.env.PHOTOSORT_HOST || "127.0.0.1";
const apiPort = process.env.PHOTOSORT_PORT || "8741";
const uiPort = Number(process.env.PHOTOSORT_UI_PORT || 5174);

export default defineConfig({
  plugins: [react()],
  server: {
    port: uiPort,
    strictPort: true,
    proxy: {
      "/api": {
        target: `http://${apiHost}:${apiPort}`,
        timeout: 600_000,
      },
    },
  },
});
