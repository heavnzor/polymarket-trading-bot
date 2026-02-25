import { NextRequest, NextResponse } from "next/server";

const SESSION_COOKIE = "dashboard_session";

function normalizeBasePath(rawValue?: string): string {
  const value = rawValue?.trim();
  if (!value || value === "/") {
    return "/";
  }

  const withLeadingSlash = value.startsWith("/") ? value : `/${value}`;
  return withLeadingSlash.replace(/\/+$/, "");
}

function toBase64Url(value: string): string {
  return Buffer.from(value, "utf-8").toString("base64url");
}

export async function POST(request: NextRequest) {
  const configuredPassword = process.env.DASHBOARD_PASSWORD?.trim();
  if (!configuredPassword) {
    return NextResponse.json(
      { detail: "DASHBOARD_PASSWORD is not configured." },
      { status: 500 }
    );
  }

  let password = "";
  try {
    const payload = (await request.json()) as { password?: string };
    password = payload.password ?? "";
  } catch {
    return NextResponse.json({ detail: "Invalid payload." }, { status: 400 });
  }

  if (password !== configuredPassword) {
    return NextResponse.json({ detail: "Invalid password." }, { status: 401 });
  }

  const response = NextResponse.json({ ok: true });
  response.cookies.set({
    name: SESSION_COOKIE,
    value: toBase64Url(configuredPassword),
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: normalizeBasePath(process.env.NEXT_BASE_PATH),
    maxAge: 60 * 60 * 24 * 30,
  });
  return response;
}
