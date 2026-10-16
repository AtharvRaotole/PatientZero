import type { NextConfig } from "next";

// NEXT_OUTPUT=export builds a static site (frontend/out) that FastAPI serves in the single-service deploy
// (see Dockerfile and DEPLOY.md). Local `npm run dev` is unaffected.
const nextConfig: NextConfig = {
  devIndicators: false,
  ...(process.env.NEXT_OUTPUT === "export" ? { output: "export" as const, images: { unoptimized: true } } : {}),
};

export default nextConfig;
