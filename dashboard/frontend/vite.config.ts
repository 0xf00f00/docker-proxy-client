import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    proxy: {
      "/api": `http://127.0.0.1:${process.env.DASHBOARD_PORT || "8080"}`,
    },
  },
  build: {
    outDir: "dist",
    // Monaco's editor core is ~3.8MB raw; it's already lazy-loaded behind the
    // Config modal so it never hits first paint. Threshold sized to let that
    // chunk pass while still flagging regressions in the main bundle.
    chunkSizeWarningLimit: 4000,
  },
});
