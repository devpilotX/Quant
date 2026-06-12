import type { NextConfig } from "next";

// Dev / single-container: proxy API+WS to FastAPI so the browser sees ONE
// origin and SameSite=Strict cookies work. In production nginx routes /api
// and /ws before Next ever sees them.
const target = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${target}/api/:path*` },
      { source: "/ws", destination: `${target}/ws` },
    ];
  },
};

export default nextConfig;
