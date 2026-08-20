const ALLOWED_PATHS = new Set([
  "v1/tasks/generate",
  "v1/knowledge/answer",
  "v1/attempts/check",
  "v1/admin/settings",
  "v1/admin/requests",
]);

type RouteContext = { params: Promise<{ path: string[] }> };
const MAX_PROXY_BODY_BYTES = 22 * 1024 * 1024;
const DEFAULT_AI_BACKEND_URL = "http://127.0.0.1:8000";

function configurationError() {
  return Response.json(
    { error: { code: "CONSOLE_NOT_CONFIGURED", message: "콘솔 서버 설정이 필요합니다." } },
    { status: 503 },
  );
}

async function proxy(request: Request, context: RouteContext) {
  const { path } = await context.params;
  const targetPath = path.join("/");
  if (!ALLOWED_PATHS.has(targetPath)) {
    return Response.json(
      { error: { code: "NOT_FOUND", message: "지원하지 않는 경로입니다." } },
      { status: 404 },
    );
  }

  const backendUrl = (process.env.AI_BACKEND_URL || DEFAULT_AI_BACKEND_URL).replace(/\/+$/, "");
  const serviceToken = process.env.AI_SERVICE_TOKEN?.trim();
  if (!serviceToken) return configurationError();

  const headers = new Headers({ Authorization: `Bearer ${serviceToken}` });
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);

  try {
    const declaredLength = Number(request.headers.get("content-length") || 0);
    if (Number.isFinite(declaredLength) && declaredLength > MAX_PROXY_BODY_BYTES) {
      return Response.json(
        { error: { code: "PAYLOAD_TOO_LARGE", message: "요청 본문이 너무 큽니다." } },
        { status: 413 },
      );
    }

    let body: BodyInit | undefined;
    if (request.method !== "GET" && request.method !== "HEAD") {
      if (request.method === "PUT" && targetPath === "v1/admin/settings") {
        const payload = await request.json() as Record<string, unknown>;
        delete payload.newServiceToken;
        body = JSON.stringify(payload);
        headers.set("Content-Type", "application/json");
      } else {
        const buffer = await request.arrayBuffer();
        if (buffer.byteLength > MAX_PROXY_BODY_BYTES) {
          return Response.json(
            { error: { code: "PAYLOAD_TOO_LARGE", message: "요청 본문이 너무 큽니다." } },
            { status: 413 },
          );
        }
        body = buffer;
      }
    }

    const upstream = await fetch(`${backendUrl}/${targetPath}`, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(90_000),
    });
    const responseHeaders = new Headers({ "Cache-Control": "no-store" });
    const upstreamType = upstream.headers.get("content-type");
    if (upstreamType) responseHeaders.set("Content-Type", upstreamType);
    for (const name of ["x-ai-provider", "x-ai-model", "x-ai-cache-status", "retry-after"]) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch (error) {
    if (error instanceof SyntaxError) {
      return Response.json(
        { error: { code: "INVALID_REQUEST", message: "요청 JSON 형식이 올바르지 않습니다." } },
        { status: 400 },
      );
    }
    return Response.json(
      { error: { code: "AI_BACKEND_UNAVAILABLE", message: "AI 백엔드에 연결할 수 없습니다." } },
      { status: 503 },
    );
  }
}

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request, context: RouteContext) {
  return proxy(request, context);
}

export async function POST(request: Request, context: RouteContext) {
  return proxy(request, context);
}

export async function PUT(request: Request, context: RouteContext) {
  return proxy(request, context);
}
