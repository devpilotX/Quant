import type { NextConfig } from "next";

// Dev / single-container: proxy API+WS to FastAPI so the browser sees ONE
// origin and SameSite=Strict cookies work. In production nginx routes /api
// and /ws before Next ever sees them.
const target = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000";

// `output: "standalone"` is for the Docker image only (small runtime bundle via
// `node server.js`). It breaks local `next start`, so enable it ONLY when the
// Docker build sets DOCKER_BUILD=1. Local `npm run build && npm start` then
// works normally; `npm run dev` is unaffected either way.
const nextConfig: NextConfig = {
  ...(process.env.DOCKER_BUILD ? { output: "standalone" as const } : {}),
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${target}/api/:path*` },
      { source: "/ws", destination: `${target}/ws` },
    ];
  },
};

export default nextConfig;
