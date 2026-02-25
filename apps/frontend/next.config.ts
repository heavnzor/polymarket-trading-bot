import type { NextConfig } from "next";

function normalizeBasePath(rawValue?: string): string | undefined {
  const value = rawValue?.trim();
  if (!value || value === "/") {
    return undefined;
  }

  const withLeadingSlash = value.startsWith("/") ? value : `/${value}`;
  return withLeadingSlash.replace(/\/+$/, "");
}

const nextConfig: NextConfig = {
  reactStrictMode: true,
  basePath: normalizeBasePath(process.env.NEXT_BASE_PATH),
};

export default nextConfig;
