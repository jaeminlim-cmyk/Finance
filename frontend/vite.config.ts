import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 빌드 결과는 백엔드가 그대로 서빙한다 (포트 하나로 실행).
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../backend/app/static", emptyOutDir: true },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
});
