import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const API_TARGET = "http://127.0.0.1:8787";

/**
 * The dev server serves the UI only; every screen reads its data from the
 * Console API. Say so up front when that API is not running, rather than
 * letting the first request fail with an opaque proxy error.
 */
function apiAvailabilityNotice(): Plugin {
  return {
    name: "eval-console:api-availability",
    apply: "serve",
    configureServer(server) {
      const report = async () => {
        try {
          const response = await fetch(`${API_TARGET}/api/v1/health`);
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          server.config.logger.info(`  Console API is up on ${API_TARGET}`);
        } catch {
          server.config.logger.warn(
            `  Console API is NOT reachable on ${API_TARGET}.\n` +
              "  Start it in another terminal: uv run ai-native-evals console\n",
          );
        }
      };
      if (server.httpServer?.listening) void report();
      else server.httpServer?.once("listening", () => void report());
    },
  };
}

export default defineConfig({
  plugins: [react(), apiAvailabilityNotice()],
  server: {
    // Bind IPv4 explicitly. The default `localhost` resolves to ::1 on Windows,
    // which makes the documented http://127.0.0.1:5173/ unreachable.
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": API_TARGET,
    },
  },
  build: {
    sourcemap: true,
  },
});
