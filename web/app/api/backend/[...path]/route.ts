import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

type RouteContext = { params: Promise<{ path: string[] }> };

async function proxy(request: NextRequest, context: RouteContext) {
  const backendBase = (process.env.API_BASE_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
  const url = new URL(backendBase);
  if (process.env.NODE_ENV === "production" && url.protocol !== "https:") {
    return NextResponse.json({ detail: "Production API_BASE_URL must use HTTPS" }, { status: 503 });
  }
  const { path } = await context.params;
  if (!path.length || path.some((part) => part === ".." || part.includes("\\"))) {
    return NextResponse.json({ detail: "Invalid backend API path" }, { status: 400 });
  }
  const target = `${backendBase}/${path.map((part) => encodeURIComponent(part)).join("/")}${new URL(request.url).search}`;
  const headers = new Headers({ Accept: "application/json" });
  const apiKey = process.env.API_SERVICE_KEY;
  if (apiKey) headers.set("Authorization", `Bearer ${apiKey}`);

  let body: ArrayBuffer | undefined;
  if (request.method === "POST") {
    const contentLength = Number(request.headers.get("content-length") ?? 0);
    if (contentLength > 64_000) return NextResponse.json({ detail: "Request payload is too large" }, { status: 413 });
    body = await request.arrayBuffer();
    if (body.byteLength > 64_000) return NextResponse.json({ detail: "Request payload is too large" }, { status: 413 });
    headers.set("Content-Type", request.headers.get("content-type") ?? "application/json");
  }

  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(request.method === "POST" ? 90_000 : 15_000),
    });
    const responseHeaders = new Headers({
      "Cache-Control": "no-store",
      "Content-Type": upstream.headers.get("content-type") ?? "application/json",
    });
    for (const header of ["x-request-id", "x-trace-id", "retry-after"]) {
      const value = upstream.headers.get(header);
      if (value) responseHeaders.set(header, value);
    }
    return new NextResponse(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch (error) {
    const detail = error instanceof DOMException && error.name === "TimeoutError" ? "Backend request timed out" : "Backend API is unavailable";
    return NextResponse.json({ detail }, { status: 503 });
  }
}

export const GET = proxy;
export const POST = proxy;