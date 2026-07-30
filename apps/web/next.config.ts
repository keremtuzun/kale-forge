import type { NextConfig } from "next";

const analysisInternalUrl = process.env.ANALYSIS_INTERNAL_URL;

const nextConfig: NextConfig = {
  output: "standalone",
  transpilePackages: ["@kale/shared-types"],
  eslint: { ignoreDuringBuilds: true },
  typescript: { ignoreBuildErrors: false },
  ...(analysisInternalUrl
    ? {
        async rewrites() {
          return [{ source: "/api/:path*", destination: `${analysisInternalUrl}/api/:path*` }];
        },
      }
    : {}),
};

export default nextConfig;
