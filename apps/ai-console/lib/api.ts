export type Connection = {
  backendUrl: string;
  authKey: string;
};

type ApiError = { error?: { code?: string; message?: string } };

export function normalizeBackend(value: string) {
  const normalized = value.trim().replace(/\/+$/, "");
  if (normalized.startsWith("/") && !normalized.startsWith("//")) {
    return normalized;
  }
  const parsed = new URL(normalized);
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("백엔드 주소는 http 또는 https로 시작해야 합니다.");
  }
  return normalized;
}

export function requireConnection(connection: Connection) {
  if (!connection.authKey.trim()) throw new Error("Bearer 키를 입력해 주세요.");
  return {
    baseUrl: normalizeBackend(connection.backendUrl),
    token: connection.authKey.trim(),
  };
}

export async function responseJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export function apiError(data: unknown, status: number) {
  const payload = data as ApiError;
  const code = payload?.error?.code;
  const message = payload?.error?.message;
  if (code && message) return `${code} · ${message}`;
  return `요청 실패 · HTTP ${status}`;
}
