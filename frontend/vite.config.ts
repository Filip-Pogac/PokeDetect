import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import basicSsl from "@vitejs/plugin-basic-ssl";

// The dev server proxies /api to the FastAPI backend so the frontend can use
// same-origin relative URLs during development.
export default defineConfig({
  // Self-signed HTTPS cert: browsers only allow camera access (getUserMedia)
  // in a secure context, and a plain http://<lan-ip> origin doesn't qualify.
  plugins: [react(), basicSsl()],
  server: {
    port: 5173,
    https: true,
    // Bind all interfaces (not just localhost) so the dev server is reachable
    // from another device on the LAN, e.g. testing camera capture on a phone.
    host: true,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
