import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// En desarrollo, /api se reenvía al backend (127.0.0.1:8000) para evitar CORS.
// La API key NUNCA va en variables VITE_*: se escribe en la interfaz y vive solo en memoria.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
});
