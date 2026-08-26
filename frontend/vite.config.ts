import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

const apiProxyTarget = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "src"),
    },
  },
  build: {
    modulePreload: {
      resolveDependencies(filename, deps, context) {
        const fileName = filename.replaceAll("\\", "/").split("/").at(-1) ?? filename;
        if (context.hostType !== "html" || !fileName.startsWith("index-")) return deps;
        return deps.filter(
          (dependency) =>
            !dependency.includes("vendor-flow") && !dependency.includes("vendor-utils"),
        );
      },
    },
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          const packagePath = id.replaceAll("\\", "/").split("/node_modules/").at(-1) ?? "";
          if (
            packagePath.startsWith("react/") ||
            packagePath.startsWith("react-dom/") ||
            packagePath.startsWith("react-router/") ||
            packagePath.startsWith("react-router-dom/")
          ) {
            return "vendor-react";
          }
          if (packagePath.startsWith("@xyflow/") || packagePath.startsWith("d3-")) {
            return "vendor-flow";
          }
          if (packagePath.startsWith("zustand/")) return "vendor-zustand";
          if (
            packagePath.startsWith("@tanstack/react-virtual/") ||
            packagePath.startsWith("lucide-react/")
          ) {
            return "vendor-utils";
          }
          return undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: apiProxyTarget,
        changeOrigin: true,
      },
      "/healthz": {
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
});
