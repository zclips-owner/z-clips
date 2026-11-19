import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The PWA is frontend-only; all API traffic goes through the
  // server-side proxy in app/api/v1/[...slug]/route.ts.
};

export default nextConfig;
