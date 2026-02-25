import { NextRequest, NextResponse } from "next/server";

const SESSION_COOKIE = "dashboard_session";

function normalizeBasePath(rawValue?: string): string {
  const value = rawValue?.trim();
  if (!value || value === "/") {
    return "";
  }

  const withLeadingSlash = value.startsWith("/") ? value : `/${value}`;
  return withLeadingSlash.replace(/\/+$/, "");
}

function toBase64Url(value: string): string {
  return btoa(value).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

const BASE_PATH = normalizeBasePath(process.env.NEXT_BASE_PATH);

function stripBasePath(pathname: string): string {
  if (!BASE_PATH) {
    return pathname || "/";
  }
  if (pathname === BASE_PATH) {
    return "/";
  }
  if (pathname.startsWith(`${BASE_PATH}/`)) {
    return pathname.slice(BASE_PATH.length);
  }
  return pathname;
}

function isPublicPath(pathname: string): boolean {
  return (
    pathname === "/access" ||
    pathname.startsWith("/access/") ||
    pathname === "/api/auth/login" ||
    pathname.startsWith("/api/auth/login/") ||
    pathname === "/favicon.ico" ||
    pathname === "/robots.txt" ||
    pathname === "/sitemap.xml" ||
    pathname.startsWith("/_next/")
  );
}

export function proxy(request: NextRequest) {
  const configuredPassword = process.env.DASHBOARD_PASSWORD?.trim();
  if (!configuredPassword) {
    return NextResponse.next();
  }

  const internalPath = stripBasePath(request.nextUrl.pathname);
  if (isPublicPath(internalPath)) {
    return NextResponse.next();
  }

  const expectedSession = toBase64Url(configuredPassword);
  const sessionCookie = request.cookies.get(SESSION_COOKIE)?.value;
  if (sessionCookie === expectedSession) {
    return NextResponse.next();
  }

  const loginUrl = request.nextUrl.clone();
  loginUrl.pathname = "/access";
  loginUrl.searchParams.set("next", internalPath);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: ["/:path*"],
};
