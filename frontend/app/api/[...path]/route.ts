// Proxy: trình duyệt gọi /api/tickets/... -> Next.js chuyển sang FastAPI /tickets/...
// Lấy từ buổi 4. Khác: backend bài này không có tiền tố /api.
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 120;

type RouteContext = { params: Promise<{ path: string[] }> };

async function proxy(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  const backend = process.env.BACKEND_INTERNAL_URL ?? "http://localhost:8000";
  const target = new URL(`/${path.join("/")}`, backend);
  target.search = request.nextUrl.search;

  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("content-length");

  try {
    const hasBody = !["GET", "HEAD"].includes(request.method);
    const response = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(120_000),
    });

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  } catch (error) {
    const timedOut = error instanceof DOMException && error.name === "TimeoutError";
    return NextResponse.json(
      { detail: timedOut ? "GenAI xử lý quá thời gian cho phép." : "Không kết nối được tới FastAPI (cổng 8000)." },
      { status: timedOut ? 504 : 502 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
