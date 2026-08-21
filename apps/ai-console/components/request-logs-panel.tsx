"use client";

import { AlertCircle, ArrowRight, Clock3, Database, LoaderCircle, RefreshCw } from "lucide-react";
import Image from "next/image";
import { useCallback, useEffect, useMemo, useState } from "react";

import { apiError, requireConnection, responseJson, type Connection } from "@/lib/api";

type CacheStatus = "" | "HIT" | "MISS" | "JOIN";

type RequestLog = {
  id: number;
  occurredAt: string;
  method: string;
  path: string;
  statusCode: number;
  durationMs: number;
  clientAddress: string;
  provider: string;
  model: string;
  cacheStatus: CacheStatus;
  fallbackProvider: string;
  outcome: string;
  errorCode: string;
  taskCount: number | null;
  providerFailureReason: string;
  providerFinishReason: string;
  providerCompletionTokens: number | null;
  providerOutput: string;
  providerOutputTruncated: boolean;
  requestPayload: string;
  requestPayloadTruncated: boolean;
  responsePayload: string;
  responsePayloadTruncated: boolean;
  requestPhotoPreview: string;
  referencePhotoPreview: string;
};

function parseLogs(value: unknown): RequestLog[] | null {
  if (!value || typeof value !== "object") return null;
  const requests = (value as { requests?: unknown }).requests;
  if (!Array.isArray(requests)) return null;
  const valid = requests.every((entry) => {
    if (!entry || typeof entry !== "object") return false;
    const item = entry as Partial<RequestLog>;
    return (
      typeof item.id === "number" &&
      typeof item.occurredAt === "string" &&
      typeof item.method === "string" &&
      typeof item.path === "string" &&
      typeof item.clientAddress === "string" &&
      typeof item.statusCode === "number" &&
      typeof item.durationMs === "number" &&
      (item.provider === undefined || typeof item.provider === "string") &&
      (item.model === undefined || typeof item.model === "string") &&
      (item.cacheStatus === undefined || ["", "HIT", "MISS", "JOIN"].includes(item.cacheStatus)) &&
      (item.fallbackProvider === undefined || typeof item.fallbackProvider === "string") &&
      (item.outcome === undefined || typeof item.outcome === "string") &&
      (item.errorCode === undefined || typeof item.errorCode === "string") &&
      (item.taskCount === undefined || item.taskCount === null || typeof item.taskCount === "number") &&
      (item.providerFailureReason === undefined || typeof item.providerFailureReason === "string") &&
      (item.providerFinishReason === undefined || typeof item.providerFinishReason === "string") &&
      (item.providerCompletionTokens === undefined || item.providerCompletionTokens === null || typeof item.providerCompletionTokens === "number") &&
      (item.providerOutput === undefined || typeof item.providerOutput === "string") &&
      (item.providerOutputTruncated === undefined || typeof item.providerOutputTruncated === "boolean") &&
      (item.requestPayload === undefined || typeof item.requestPayload === "string") &&
      (item.requestPayloadTruncated === undefined || typeof item.requestPayloadTruncated === "boolean") &&
      (item.responsePayload === undefined || typeof item.responsePayload === "string") &&
      (item.responsePayloadTruncated === undefined || typeof item.responsePayloadTruncated === "boolean") &&
      (item.requestPhotoPreview === undefined || typeof item.requestPhotoPreview === "string") &&
      (item.referencePhotoPreview === undefined || typeof item.referencePhotoPreview === "string")
    );
  });
  if (!valid) return null;
  return requests.map((entry) => {
    const item = entry as Partial<RequestLog> & Pick<RequestLog, "id" | "occurredAt" | "method" | "path" | "statusCode" | "durationMs" | "clientAddress">;
    return {
      ...item,
      provider: item.provider ?? "",
      model: item.model ?? "",
      cacheStatus: item.cacheStatus ?? "",
      fallbackProvider: item.fallbackProvider ?? "",
      outcome: item.outcome ?? "",
      errorCode: item.errorCode ?? "",
      taskCount: item.taskCount ?? null,
      providerFailureReason: item.providerFailureReason ?? "",
      providerFinishReason: item.providerFinishReason ?? "",
      providerCompletionTokens: item.providerCompletionTokens ?? null,
      providerOutput: item.providerOutput ?? "",
      providerOutputTruncated: item.providerOutputTruncated ?? false,
      requestPayload: item.requestPayload ?? "",
      requestPayloadTruncated: item.requestPayloadTruncated ?? false,
      responsePayload: item.responsePayload ?? "",
      responsePayloadTruncated: item.responsePayloadTruncated ?? false,
      requestPhotoPreview: item.requestPhotoPreview ?? "",
      referencePhotoPreview: item.referencePhotoPreview ?? "",
    };
  });
}

function requestName(entry: RequestLog) {
  if (entry.path === "/v1/tasks/generate") return "태스크 생성";
  if (entry.path === "/v1/attempts/check") return "사진 검증";
  if (entry.path === "/v1/knowledge/answer") return "매장 정보 질문";
  if (entry.path === "/v1/admin/settings") {
    return entry.method === "PUT" ? "서버 설정 저장" : "서버 설정 조회";
  }
  return entry.path;
}

function localTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function statusClass(code: number) {
  if (code >= 500) return "error";
  if (code >= 400) return "warning";
  return "success";
}

function statusLabel(code: number) {
  if (code === 400) return "요청 오류";
  if (code === 401) return "인증 실패";
  if (code === 422) return "사진 오류";
  if (code === 503) return "AI 장애";
  if (code >= 500) return "서버 오류";
  if (code >= 400) return "처리 실패";
  return "정상";
}

function outcomeLabel(entry: RequestLog) {
  if (entry.outcome === "TASKS_GENERATED") return "태스크 생성";
  if (entry.outcome === "KNOWLEDGE_ANSWERED") return "정보 답변";
  if (entry.outcome === "PASS") return "PASS";
  if (entry.outcome === "RETAKE") return "RETAKE";
  if (entry.outcome === "SETTINGS_READ") return "설정 조회";
  if (entry.outcome === "SETTINGS_UPDATED") return "설정 변경";
  if (entry.outcome === "ERROR" || entry.statusCode >= 400) return "실패";
  return "기록 없음";
}

function outcomeDetail(entry: RequestLog) {
  if (entry.errorCode) return entry.errorCode;
  if (entry.taskCount !== null) return `${entry.taskCount}개 생성`;
  if (entry.cacheStatus === "HIT") return "저장된 응답 사용";
  return "";
}

function durationLabel(milliseconds: number) {
  return milliseconds < 1000 ? `${milliseconds}ms` : `${(milliseconds / 1000).toFixed(2)}s`;
}

function prettyPayload(value: string) {
  if (!value) return "";
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

export function RequestLogsPanel({ connection }: { connection: Connection }) {
  const [logs, setLogs] = useState<RequestLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadLogs = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const { baseUrl } = requireConnection(connection);
      const response = await fetch(`${baseUrl}/v1/admin/requests`, {
        cache: "no-store",
      });
      const data = await responseJson(response);
      if (!response.ok) throw new Error(apiError(data, response.status));
      const parsed = parseLogs(data);
      if (!parsed) throw new Error("요청 기록 응답 형식이 올바르지 않습니다.");
      setLogs(parsed);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "요청 기록을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [connection]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadLogs(), 0);
    return () => window.clearTimeout(timer);
  }, [loadLogs]);

  const metrics = useMemo(() => {
    const total = logs.length;
    const sortedDurations = logs.map((entry) => entry.durationMs).sort((a, b) => a - b);
    const p95 = total ? sortedDurations[Math.max(0, Math.ceil(total * 0.95) - 1)] : 0;
    const slowest = total ? Math.max(...logs.map((entry) => entry.durationMs)) : 0;
    const errors = logs.filter((entry) => entry.statusCode >= 400).length;
    const successes = logs.filter((entry) => entry.statusCode < 400).length;
    const cacheTracked = logs.filter((entry) => Boolean(entry.cacheStatus)).length;
    const cacheHits = logs.filter((entry) => entry.cacheStatus === "HIT").length;
    const fallbacks = logs.filter((entry) => Boolean(entry.fallbackProvider)).length;
    return {
      total,
      p95,
      slowest,
      errors,
      successRate: total ? Math.round(successes / total * 100) : 0,
      cacheHits,
      cacheTracked,
      fallbacks,
    };
  }, [logs]);

  const durationScale = Math.max(metrics.slowest, 1);

  return (
    <section className="logs-page">
      <header className="section-header">
        <div>
          <span className="endpoint">REQUEST HISTORY</span>
          <h1>API 요청 기록</h1>
          <p>최근 100건 · 전체 로그 64 MiB 상한 · 사진은 원본 없이 장당 200KB·640px 미리보기만 저장</p>
        </div>
        <button className="button secondary" onClick={loadLogs} disabled={loading}>
          <RefreshCw className={loading ? "spin" : ""} size={15} /> 새로고침
        </button>
      </header>

      <div className="log-metrics" aria-label="요청 기록 요약">
        <span><small>REQUESTS</small><b>{metrics.total}</b></span>
        <span><small>SUCCESS</small><b>{metrics.successRate}%</b></span>
        <span><small>P95 LATENCY</small><b>{durationLabel(metrics.p95)}</b></span>
        <span className={metrics.errors ? "has-error" : ""}><small>ERRORS</small><b>{metrics.errors}</b></span>
        <span><small>CACHE HITS</small><b>{metrics.cacheHits}<em>/ {metrics.cacheTracked}</em></b></span>
        <span className={metrics.fallbacks ? "has-warning" : ""}><small>FALLBACKS</small><b>{metrics.fallbacks}</b></span>
      </div>

      {error && <div className="notice danger log-notice"><AlertCircle size={16} /> {error}</div>}

      <div className="log-table">
        <div className="log-row log-head" aria-hidden="true">
          <span>시각 / ID</span><span>요청</span><span>실행 경로</span><span>결과</span><span>백엔드 연결 주소</span><span>HTTP</span><span>소요 시간</span>
        </div>
        {!loading && !error && logs.length === 0 && (
          <div className="log-empty">
            <Clock3 size={21} />
            <strong>아직 요청 기록이 없습니다.</strong>
            <span>태스크 생성이나 사진 검증을 실행하면 여기에 표시됩니다.</span>
          </div>
        )}
        {loading && logs.length === 0 && (
          <div className="log-empty"><LoaderCircle className="spin" size={22} /><strong>기록 불러오는 중</strong></div>
        )}
        {logs.map((entry) => (
          <article className="log-row" key={entry.id}>
            <span className="log-time"><time dateTime={entry.occurredAt}>{localTime(entry.occurredAt)}</time><small>#{entry.id}</small></span>
            <span className="log-operation">
              <span><b className={`method method-${entry.method.toLowerCase()}`}>{entry.method}</b><strong>{requestName(entry)}</strong></span>
              <small>{entry.path}</small>
            </span>
            <span className="execution-route">
              {entry.provider ? (
                <span className="provider-line">
                  <b>{entry.provider}</b>
                  {entry.fallbackProvider && <><ArrowRight size={11} aria-hidden="true" /><b className="fallback-provider">{entry.fallbackProvider}</b></>}
                </span>
              ) : <span className="missing-value">기록 없음</span>}
              <small>{entry.model || "이전 로그"}</small>
              {entry.cacheStatus && <em className={`cache-badge cache-${entry.cacheStatus.toLowerCase()}`}><Database size={10} /> {entry.cacheStatus}</em>}
            </span>
            <span className={`log-outcome outcome-${entry.outcome.toLowerCase() || "unknown"}`}>
              <strong>{outcomeLabel(entry)}</strong>
              {outcomeDetail(entry) && <small>{outcomeDetail(entry)}</small>}
            </span>
            <span className="client-address">{entry.clientAddress || "기록 없음"}</span>
            <span className="http-status"><b className={`status-code ${statusClass(entry.statusCode)}`}>{entry.statusCode}</b><small>{statusLabel(entry.statusCode)}</small></span>
            <span className={`duration ${entry.durationMs >= 10_000 ? "slow" : ""}`}>
              <b>{durationLabel(entry.durationMs)}</b>
              <i><span style={{ width: `${Math.max(2, entry.durationMs / durationScale * 100)}%` }} /></i>
            </span>
            {(entry.requestPayload || entry.responsePayload || entry.requestPhotoPreview || entry.referencePhotoPreview) && (
              <details className="exchange-detail">
                <summary>
                  실제 요청 · 응답 보기
                  <small>
                    JSON 자동 정렬
                    {(entry.requestPayloadTruncated || entry.responsePayloadTruncated) && " · 일부 잘림"}
                  </small>
                </summary>
                <div className="exchange-grid">
                  <section className="payload-panel request-payload">
                    <header>
                      <b>REQUEST</b>
                      <small>{entry.requestPayloadTruncated ? "128,000자에서 잘림" : "검증·마스킹된 입력"}</small>
                    </header>
                    {entry.requestPayload ? <pre>{prettyPayload(entry.requestPayload)}</pre> : <p>요청 본문 없음</p>}
                  </section>
                  <section className="payload-panel response-payload">
                    <header>
                      <b>RESPONSE</b>
                      <small>{entry.responsePayloadTruncated ? "128,000자에서 잘림" : `HTTP ${entry.statusCode}`}</small>
                    </header>
                    {entry.responsePayload ? <pre>{prettyPayload(entry.responsePayload)}</pre> : <p>응답 본문 없음</p>}
                  </section>
                </div>
                {(entry.requestPhotoPreview || entry.referencePhotoPreview) && (
                  <div className="logged-photo-grid">
                    {entry.requestPhotoPreview && (
                      <figure>
                        <figcaption><b>제출 사진</b><small>로그 미리보기 · 최대 640px</small></figcaption>
                        <Image src={entry.requestPhotoPreview} alt="요청에 제출된 검증 사진" width={640} height={640} unoptimized />
                      </figure>
                    )}
                    {entry.referencePhotoPreview && (
                      <figure>
                        <figcaption><b>모범 사진</b><small>로그 미리보기 · 최대 640px</small></figcaption>
                        <Image src={entry.referencePhotoPreview} alt="검증에 사용된 모범 사진" width={640} height={640} unoptimized />
                      </figure>
                    )}
                  </div>
                )}
              </details>
            )}
            {(entry.fallbackProvider || entry.providerFailureReason || entry.providerOutput) && (
              <details className="provider-output-detail">
                <summary>
                  제공사 실패 상세 보기
                  <small>
                    {entry.providerFailureReason || "상세 수집 이전 로그"}
                    {entry.providerFinishReason && ` · finish=${entry.providerFinishReason}`}
                    {entry.providerCompletionTokens !== null && ` · ${entry.providerCompletionTokens.toLocaleString()} tokens`}
                    {entry.providerOutputTruncated && " · 64,000자에서 잘림"}
                  </small>
                </summary>
                {entry.providerOutput ? (
                  <pre>{entry.providerOutput}</pre>
                ) : (
                  <p className="provider-output-missing">
                    제공사가 응답 본문을 반환하지 않았거나, 출력 수집 기능 적용 이전의 기록입니다.
                    HTTP 상태와 실패 원인은 위 진단 정보를 확인해 주세요.
                  </p>
                )}
              </details>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
