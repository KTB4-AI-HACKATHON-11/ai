"use client";

import Image from "next/image";
import {
  AlertCircle,
  CheckCircle2,
  Database,
  Download,
  FileImage,
  Gauge,
  ImagePlus,
  LoaderCircle,
  Play,
  RefreshCw,
  Trash2,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { apiError, requireConnection, responseJson, type Connection } from "@/lib/api";
import {
  deleteBenchmarkRecord,
  listBenchmarkRecords,
  photoFile,
  putBenchmarkRecord,
  storedPhoto,
  type BenchmarkRecord,
  type BenchmarkRun,
  type BenchmarkStatus,
  type StoredPhoto,
} from "@/lib/benchmark-store";

type EffectiveSettings = {
  provider: string;
  model: string;
  revision: number;
  effectivePrompts: {
    photoCheck: string;
    photoCheckWithReference: string;
  };
};

type VerificationResult =
  | { status: "PASS"; reason: string }
  | { status: "RETAKE"; reason: string; fix: string };

const PHOTO_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const MAX_PHOTO_BYTES = 10 * 1024 * 1024;
const MAX_RETENTION_BYTES = 1024 * 1024 * 1024;
const PARALLEL_RUNS = 4;

function validSettings(value: unknown): value is EffectiveSettings {
  if (!value || typeof value !== "object") return false;
  const settings = value as Record<string, unknown>;
  const prompts = settings.effectivePrompts as Record<string, unknown> | undefined;
  return (
    typeof settings.provider === "string" &&
    typeof settings.model === "string" &&
    typeof settings.revision === "number" &&
    !!prompts &&
    typeof prompts.photoCheck === "string" &&
    typeof prompts.photoCheckWithReference === "string"
  );
}

function validResult(value: unknown): value is VerificationResult {
  if (!value || typeof value !== "object") return false;
  const result = value as Record<string, unknown>;
  return (
    (result.status === "PASS" || result.status === "RETAKE") &&
    typeof result.reason === "string" &&
    (result.status !== "RETAKE" || typeof result.fix === "string")
  );
}

function photoMetadata(photo: StoredPhoto | null) {
  if (!photo) return null;
  return {
    name: photo.name,
    type: photo.type,
    sizeBytes: photo.size,
    lastModified: new Date(photo.lastModified).toISOString(),
  };
}

function formatDuration(durationMs: number) {
  if (durationMs < 1000) return `${durationMs}ms`;
  return `${(durationMs / 1000).toFixed(2)}s`;
}

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(bytes < 100 * 1024 * 1024 ? 1 : 0)}MB`;
}

export function BenchmarkPanel({
  connection,
  revision,
}: {
  connection: Connection;
  revision: number;
}) {
  const [records, setRecords] = useState<BenchmarkRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [runningIds, setRunningIds] = useState<Set<string>>(new Set());
  const [bulkRunning, setBulkRunning] = useState(false);
  const [completedRuns, setCompletedRuns] = useState(0);

  useEffect(() => {
    let active = true;
    listBenchmarkRecords()
      .then((values) => {
        if (active) setRecords(values);
      })
      .catch((caught) => {
        if (active) setError(caught instanceof Error ? caught.message : "벤치마크를 불러오지 못했습니다.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [revision]);

  const metrics = useMemo(() => {
    const runs = records.flatMap((record) => record.lastRun ? [record.lastRun] : []);
    const measured = runs.filter((run) => run.actualStatus !== null);
    const matched = runs.filter((run) => run.matched).length;
    const modelNames = [...new Set(measured.map((run) => [run.provider, run.model].filter(Boolean).join(" / ")).filter(Boolean))];
    const averageDurationMs = measured.length
      ? Math.round(measured.reduce((total, run) => total + run.durationMs, 0) / measured.length)
      : 0;
    const retainedBytes = records.reduce(
      (total, record) => total + (record.photo?.size ?? 0) + (record.referencePhoto?.size ?? 0),
      0,
    );
    return {
      matched,
      successRate: records.length ? Math.round((matched / records.length) * 100) : 0,
      averageDurationMs,
      modelLabel: modelNames.length === 0 ? "실행 전" : modelNames.length === 1 ? modelNames[0] : `혼합 모델 ${modelNames.length}개`,
      models: modelNames,
      runCount: runs.length,
      retainedBytes,
    };
  }, [records]);

  async function fetchSettings(): Promise<EffectiveSettings> {
    const { baseUrl } = requireConnection(connection);
    const response = await fetch(`${baseUrl}/v1/admin/settings`, {
      cache: "no-store",
    });
    const data = await responseJson(response);
    if (!response.ok) throw new Error(apiError(data, response.status));
    if (!validSettings(data)) throw new Error("서버 설정 응답에 실행 프롬프트 정보가 없습니다.");
    return data;
  }

  async function persistRecord(record: BenchmarkRecord) {
    await putBenchmarkRecord(record);
    setRecords((current) => current.map((item) => item.id === record.id ? record : item));
  }

  async function updateRecord(record: BenchmarkRecord, patch: Partial<BenchmarkRecord>) {
    setError("");
    const next: BenchmarkRecord = {
      ...record,
      ...patch,
      updatedAt: new Date().toISOString(),
    };
    try {
      await persistRecord(next);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "벤치마크를 저장하지 못했습니다.");
    }
  }

  async function choosePhoto(record: BenchmarkRecord, target: "photo" | "referencePhoto", file: File | null) {
    if (!file) return;
    if (!PHOTO_TYPES.has(file.type)) {
      setError("사진은 JPEG, PNG 또는 WebP 형식이어야 합니다.");
      return;
    }
    if (file.size > MAX_PHOTO_BYTES) {
      setError("사진은 파일당 10MB 이하여야 합니다.");
      return;
    }
    const replacingBytes = record[target]?.size ?? 0;
    if (metrics.retainedBytes - replacingBytes + file.size > MAX_RETENTION_BYTES) {
      setError("벤치마크 사진 보관 한도 1GB를 초과합니다. 기존 사진이나 케이스를 삭제해 주세요.");
      return;
    }
    await updateRecord(record, { [target]: storedPhoto(file), lastRun: null });
  }

  async function executeRecord(record: BenchmarkRecord, settings: EffectiveSettings) {
    const started = performance.now();
    const initialEffectivePrompt = record.referencePhoto
      ? settings.effectivePrompts.photoCheckWithReference
      : settings.effectivePrompts.photoCheck;
    let response: Response | null = null;
    let result: VerificationResult | null = null;
    let runError = "";

    setRunningIds((current) => new Set(current).add(record.id));
    try {
      if (!record.photo) throw new Error("인증 사진을 먼저 업로드해 주세요.");
      const { baseUrl } = requireConnection(connection);
      const form = new FormData();
      form.append("task", JSON.stringify(record.task));
      const photo = photoFile(record.photo);
      form.append("photo", photo, photo.name);
      if (record.referencePhoto) {
        const reference = photoFile(record.referencePhoto);
        form.append("referencePhoto", reference, reference.name);
      }
      response = await fetch(`${baseUrl}/v1/attempts/check`, {
        method: "POST",
        body: form,
      });
      const data = await responseJson(response);
      if (!response.ok) throw new Error(apiError(data, response.status));
      if (!validResult(data)) throw new Error("검증 응답 형식이 계약과 다릅니다.");
      result = data;
    } catch (caught) {
      runError = caught instanceof Error ? caught.message : "사진 검증에 실패했습니다.";
    }

    const actualStatus = result?.status ?? null;
    const run: BenchmarkRun = {
      expectedStatus: record.expectedStatus,
      actualStatus,
      matched: actualStatus === record.expectedStatus,
      reason: result?.reason ?? "",
      fix: result?.status === "RETAKE" ? result.fix : "",
      error: runError,
      durationMs: Math.max(0, Math.round(performance.now() - started)),
      ranAt: new Date().toISOString(),
      provider: response?.headers.get("x-ai-provider") || settings.provider,
      model: response?.headers.get("x-ai-model") || settings.model,
      cacheStatus: response?.headers.get("x-ai-cache-status") || "",
      settingsRevision: settings.revision,
      usedReferencePhoto: Boolean(record.referencePhoto),
      initialEffectivePrompt,
    };
    const next = { ...record, lastRun: run, updatedAt: run.ranAt };
    try {
      await persistRecord(next);
    } finally {
      setRunningIds((current) => {
        const nextIds = new Set(current);
        nextIds.delete(record.id);
        return nextIds;
      });
    }
  }

  async function runOne(record: BenchmarkRecord) {
    setError("");
    try {
      const settings = await fetchSettings();
      await executeRecord(record, settings);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "벤치마크 실행에 실패했습니다.");
    }
  }

  async function runAll() {
    if (!records.length) return;
    setError("");
    setBulkRunning(true);
    setCompletedRuns(0);
    try {
      const settings = await fetchSettings();
      const snapshot = [...records];
      let cursor = 0;
      async function worker() {
        while (cursor < snapshot.length) {
          const currentIndex = cursor;
          cursor += 1;
          await executeRecord(snapshot[currentIndex], settings);
          setCompletedRuns((value) => value + 1);
        }
      }
      await Promise.all(Array.from({ length: Math.min(PARALLEL_RUNS, snapshot.length) }, () => worker()));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "전체 벤치마크 실행에 실패했습니다.");
    } finally {
      setBulkRunning(false);
    }
  }

  async function removeRecord(record: BenchmarkRecord) {
    if (!window.confirm(`'${record.task.title}' 벤치마크를 삭제할까요?`)) return;
    setError("");
    try {
      await deleteBenchmarkRecord(record.id);
      setRecords((current) => current.filter((item) => item.id !== record.id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "벤치마크를 삭제하지 못했습니다.");
    }
  }

  function downloadReport() {
    const report = {
      format: "flowcheck-ai-benchmark",
      formatVersion: 1,
      generatedAt: new Date().toISOString(),
      summary: {
        totalCases: records.length,
        executedCases: metrics.runCount,
        matchedCases: metrics.matched,
        successRatePercent: metrics.successRate,
        averageDurationMs: metrics.averageDurationMs,
        models: metrics.models,
        retainedPhotoBytes: metrics.retainedBytes,
        retainedPhotoLimitBytes: MAX_RETENTION_BYTES,
      },
      cases: records.map((record, index) => ({
        sequence: index + 1,
        id: record.id,
        createdAt: record.createdAt,
        updatedAt: record.updatedAt,
        task: record.task,
        expectedStatus: record.expectedStatus,
        photo: photoMetadata(record.photo),
        referencePhoto: photoMetadata(record.referencePhoto),
        result: record.lastRun ? {
          expectedStatusAtRun: record.lastRun.expectedStatus,
          actualStatus: record.lastRun.actualStatus,
          matched: record.lastRun.matched,
          reason: record.lastRun.reason,
          fix: record.lastRun.fix,
          error: record.lastRun.error,
          durationMs: record.lastRun.durationMs,
          ranAt: record.lastRun.ranAt,
          provider: record.lastRun.provider,
          model: record.lastRun.model,
          cacheStatus: record.lastRun.cacheStatus || "NONE",
          settingsRevision: record.lastRun.settingsRevision,
          usedReferencePhoto: record.lastRun.usedReferencePhoto,
          initialEffectivePrompt: record.lastRun.initialEffectivePrompt,
          promptNote: "실행 시 최초 AI 호출에 사용된 유효 프롬프트입니다. 형식 보정 재시도 시에는 서버의 보정 지시가 추가될 수 있습니다.",
        } : null,
      })),
    };
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+$/, "").replace("T", "-");
    anchor.href = url;
    anchor.download = `flowcheck-benchmark-${stamp}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  if (loading) {
    return <div className="empty-panel"><LoaderCircle className="spin" size={30} /><p>저장된 벤치마크를 불러오는 중입니다.</p></div>;
  }

  return (
    <section className="benchmark-page">
      <header className="section-header benchmark-heading">
        <div>
          <span className="endpoint">PHOTO VERIFICATION BENCHMARK</span>
          <h1>벤치마크</h1>
          <p><Database size={12} /> 사진과 판정은 이 브라우저에 자동 저장됩니다.</p>
        </div>
        <div className="header-actions">
          <button className="button secondary" onClick={downloadReport} disabled={!records.length}>
            <Download size={15} /> 상세 보고서
          </button>
          <button className="button primary" onClick={runAll} disabled={!records.length || bulkRunning || runningIds.size > 0}>
            {bulkRunning ? <LoaderCircle className="spin" size={15} /> : <Play size={15} />}
            {bulkRunning ? `${completedRuns} / ${records.length}` : "전체 실행"}
          </button>
        </div>
      </header>

      <div className="benchmark-scoreboard" aria-label="벤치마크 요약">
        <div className="benchmark-score">
          <span>MATCH RATE</span>
          <b>{records.length ? `${metrics.successRate}%` : "—"}</b>
          <small>{metrics.matched} / {records.length} 일치</small>
        </div>
        <div><span>MODEL</span><b title={metrics.modelLabel}>{metrics.modelLabel}</b><small>실제 응답 기준</small></div>
        <div><span>AVG TIME</span><b>{metrics.averageDurationMs ? formatDuration(metrics.averageDurationMs) : "—"}</b><small>응답 완료 건 평균</small></div>
        <div><span>RETENTION</span><b>{formatBytes(metrics.retainedBytes)} / 1GB</b><small>IndexedDB · 사진/결과 자동 저장</small></div>
      </div>

      {error && <div className="notice danger benchmark-notice" role="alert"><AlertCircle size={15} /> {error}</div>}

      {records.length === 0 ? (
        <div className="empty-panel benchmark-empty">
          <Gauge size={34} />
          <strong>아직 벤치마크가 없습니다.</strong>
          <p>API 테스트의 PHOTO 태스크에서 ‘벤치마크 추가’를 누르세요.</p>
        </div>
      ) : (
        <div className="benchmark-table">
          <div className="benchmark-row benchmark-row-head" aria-hidden="true">
            <span>CASE</span><span>기대 판정</span><span>인증 사진</span><span>모범</span><span>실제 판정</span><span>시간</span><span>실행</span>
          </div>
          {records.map((record, index) => {
            const isRunning = runningIds.has(record.id);
            const run = record.lastRun;
            return (
              <article className={`benchmark-case ${run ? run.matched ? "matched" : "mismatched" : ""}`} key={record.id}>
                <div className="benchmark-row">
                  <div className="benchmark-case-name">
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <div><strong title={record.task.title}>{record.task.title}</strong><small title={record.task.rule}>{record.task.rule}</small></div>
                  </div>
                  <StatusChoice
                    value={record.expectedStatus}
                    disabled={isRunning || bulkRunning}
                    onChange={(expectedStatus) => updateRecord(record, { expectedStatus, lastRun: null })}
                  />
                  <CompactPhoto
                    label="인증 사진"
                    photo={record.photo}
                    disabled={isRunning || bulkRunning}
                    required
                    onChange={(file) => choosePhoto(record, "photo", file)}
                  />
                  <CompactPhoto
                    label="모범 사진"
                    photo={record.referencePhoto}
                    disabled={isRunning || bulkRunning}
                    onChange={(file) => choosePhoto(record, "referencePhoto", file)}
                    onClear={record.referencePhoto ? () => updateRecord(record, { referencePhoto: null, lastRun: null }) : undefined}
                  />
                  <div className="benchmark-actual">
                    {run?.error ? <span className="run-error"><AlertCircle size={13} /> ERROR</span>
                      : run?.actualStatus === "PASS" ? <span className="run-pass"><CheckCircle2 size={13} /> PASS</span>
                        : run?.actualStatus === "RETAKE" ? <span className="run-retake"><XCircle size={13} /> RETAKE</span>
                          : <span className="run-pending">미실행</span>}
                    {run && <small>{run.matched ? "일치" : "불일치"}</small>}
                  </div>
                  <div className="benchmark-time">
                    <b>{run ? formatDuration(run.durationMs) : "—"}</b>
                    <small>{run?.cacheStatus || ""}</small>
                  </div>
                  <div className="benchmark-actions">
                    <button className="icon-button" onClick={() => runOne(record)} disabled={isRunning || bulkRunning} aria-label={`${record.task.title} 실행`}>
                      {isRunning ? <LoaderCircle className="spin" size={14} /> : <Play size={14} />}
                    </button>
                    <button className="icon-button remove" onClick={() => removeRecord(record)} disabled={isRunning || bulkRunning} aria-label={`${record.task.title} 삭제`}><Trash2 size={14} /></button>
                  </div>
                </div>
                <details className="benchmark-detail">
                  <summary>상세 정보 <small>{run ? `${run.provider || "—"} · ${run.model || "—"}` : "수행 안내와 판정 근거"}</small></summary>
                  <div>
                    <section><span>수행 안내</span><p>{record.task.instruction}</p></section>
                    <section><span>판정 근거</span><p>{run?.error || run?.reason || "실행 결과가 없습니다."}</p>{run?.fix && <small><RefreshCw size={11} /> {run.fix}</small>}</section>
                  </div>
                </details>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

function StatusChoice({
  value,
  disabled,
  onChange,
}: {
  value: BenchmarkStatus;
  disabled: boolean;
  onChange: (value: BenchmarkStatus) => void;
}) {
  return (
    <div className="status-choice" aria-label="기대 판정">
      <button className={value === "PASS" ? "active pass" : ""} disabled={disabled} onClick={() => onChange("PASS")}>PASS</button>
      <button className={value === "RETAKE" ? "active retake" : ""} disabled={disabled} onClick={() => onChange("RETAKE")}>RETAKE</button>
    </div>
  );
}

function CompactPhoto({
  label,
  photo,
  required = false,
  disabled,
  onChange,
  onClear,
}: {
  label: string;
  photo: StoredPhoto | null;
  required?: boolean;
  disabled: boolean;
  onChange: (file: File | null) => void;
  onClear?: () => void;
}) {
  return (
    <div className="compact-photo">
      <label title={photo?.name || `${label} 업로드`}>
        <input className="sr-only" type="file" accept="image/jpeg,image/png,image/webp" disabled={disabled} onChange={(event) => onChange(event.target.files?.[0] || null)} />
        {photo ? <PhotoThumbnail key={`${photo.name}-${photo.lastModified}-${photo.size}`} photo={photo} /> : <span><ImagePlus size={14} /></span>}
        <em>{photo ? photo.name : required ? "사진 필요" : "선택"}</em>
      </label>
      {photo && onClear && <button onClick={onClear} disabled={disabled} aria-label={`${label} 제거`}><XCircle size={12} /></button>}
      {!photo && required && <FileImage className="required-photo" size={10} aria-hidden="true" />}
    </div>
  );
}

function PhotoThumbnail({ photo }: { photo: StoredPhoto }) {
  const [previewUrl] = useState(() => URL.createObjectURL(photo.blob));
  useEffect(() => () => URL.revokeObjectURL(previewUrl), [previewUrl]);
  return <Image src={previewUrl} alt="" width={36} height={36} unoptimized />;
}
