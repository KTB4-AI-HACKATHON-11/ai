"use client";

import {
  AlertCircle,
  CheckCircle2,
  LoaderCircle,
  Plus,
  RefreshCw,
  Save,
  ShieldCheck,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { apiError, requireConnection, responseJson, type Connection } from "@/lib/api";

type Provider = "OPENROUTER" | "CEREBRAS";

type AvailableModel = {
  provider: Provider;
  id: string;
  label: string;
};

type ServerSettings = {
  provider: Provider;
  model: string;
  prompts: {
    taskGeneration: string;
    photoCheck: string;
  };
  openrouterModels: string[];
  fallbackModel: string;
  cacheHitsEnabled: boolean;
  cacheTtlSeconds: number;
  fallbackEnabled: boolean;
  requestLogsEnabled: boolean;
  providerKeys: {
    openrouter: boolean;
    cerebras: boolean;
  };
  availableModels: AvailableModel[];
  revision: number;
};

function isSettings(value: unknown): value is ServerSettings {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<ServerSettings>;
  return (
    (item.provider === "OPENROUTER" || item.provider === "CEREBRAS") &&
    typeof item.model === "string" &&
    typeof item.prompts?.taskGeneration === "string" &&
    typeof item.prompts?.photoCheck === "string" &&
    Array.isArray(item.openrouterModels) &&
    item.openrouterModels.every((model) => typeof model === "string") &&
    typeof item.fallbackModel === "string" &&
    typeof item.cacheHitsEnabled === "boolean" &&
    typeof item.cacheTtlSeconds === "number" &&
    typeof item.fallbackEnabled === "boolean" &&
    typeof item.requestLogsEnabled === "boolean" &&
    Array.isArray(item.availableModels) &&
    typeof item.revision === "number"
  );
}

export function SettingsPanel({
  connection,
}: {
  connection: Connection;
}) {
  const [settings, setSettings] = useState<ServerSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [newOpenRouterModel, setNewOpenRouterModel] = useState("");

  const loadSettings = useCallback(async () => {
    setError("");
    setSaved("");
    setLoading(true);
    try {
      const { baseUrl, token } = requireConnection(connection);
      const response = await fetch(`${baseUrl}/v1/admin/settings`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await responseJson(response);
      if (!response.ok) throw new Error(apiError(data, response.status));
      if (!isSettings(data)) throw new Error("설정 응답 형식이 올바르지 않습니다.");
      setSettings(data);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "설정을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [connection]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadSettings(), 0);
    return () => window.clearTimeout(timer);
  }, [loadSettings]);

  function chooseModel(model: AvailableModel) {
    setSettings((current) => current && { ...current, provider: model.provider, model: model.id });
    setSaved("");
  }

  function addOpenRouterModel() {
    if (!settings) return;
    const model = newOpenRouterModel.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9._:/-]*$/.test(model) || model.length > 120) {
      setError("OpenRouter 모델 ID 형식이 올바르지 않습니다.");
      return;
    }
    if (settings.openrouterModels.includes(model)) {
      setError("이미 등록된 OpenRouter 모델입니다.");
      return;
    }
    if (settings.openrouterModels.length >= 20) {
      setError("OpenRouter 모델은 최대 20개까지 등록할 수 있습니다.");
      return;
    }
    setSettings({
      ...settings,
      openrouterModels: [...settings.openrouterModels, model],
      availableModels: [
        ...settings.availableModels.filter((item) => item.provider === "OPENROUTER"),
        { provider: "OPENROUTER", id: model, label: `${model} · OpenRouter` },
        ...settings.availableModels.filter((item) => item.provider !== "OPENROUTER"),
      ],
    });
    setNewOpenRouterModel("");
    setError("");
    setSaved("");
  }

  function removeOpenRouterModel(model: string) {
    if (!settings) return;
    if (settings.provider === "OPENROUTER" && settings.model === model) {
      setError("현재 사용 중인 모델은 다른 모델을 선택한 뒤 삭제해 주세요.");
      return;
    }
    if (settings.fallbackModel === model) {
      setError("현재 폴백 모델은 다른 폴백 모델을 선택한 뒤 삭제해 주세요.");
      return;
    }
    if (settings.openrouterModels.length === 1) {
      setError("OpenRouter 모델은 최소 1개가 필요합니다.");
      return;
    }
    setSettings({
      ...settings,
      openrouterModels: settings.openrouterModels.filter((item) => item !== model),
      availableModels: settings.availableModels.filter(
        (item) => item.provider !== "OPENROUTER" || item.id !== model,
      ),
    });
    setError("");
    setSaved("");
  }

  async function saveSettings() {
    if (!settings) return;
    setError("");
    setSaved("");
    setSaving(true);
    try {
      const { baseUrl, token } = requireConnection(connection);
      const response = await fetch(`${baseUrl}/v1/admin/settings`, {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          provider: settings.provider,
          model: settings.model,
          prompts: settings.prompts,
          openrouterModels: settings.openrouterModels,
          fallbackModel: settings.fallbackModel,
          cacheHitsEnabled: settings.cacheHitsEnabled,
          cacheTtlSeconds: settings.cacheTtlSeconds,
          fallbackEnabled: settings.fallbackEnabled,
          requestLogsEnabled: settings.requestLogsEnabled,
        }),
      });
      const data = await responseJson(response);
      if (!response.ok) throw new Error(apiError(data, response.status));
      if (!isSettings(data)) throw new Error("설정 응답 형식이 올바르지 않습니다.");
      setSettings(data);
      setSaved(`revision ${data.revision} 저장됨`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "설정을 저장하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }

  if (!settings) {
    return (
      <section className="empty-panel">
        <SettingsEmptyIcon />
        <strong>{loading ? "설정 불러오는 중" : "서버 인증 필요"}</strong>
        <p>{loading
          ? "현재 런타임 설정을 확인합니다."
          : "콘솔 서버의 백엔드 연결 설정을 확인해 주세요."}</p>
        {!loading && <button className="button secondary" onClick={loadSettings}><RefreshCw size={15} /> 불러오기</button>}
        {loading && <LoaderCircle className="spin" size={20} />}
        {error && <div className="notice danger"><AlertCircle size={16} /> {error}</div>}
      </section>
    );
  }

  const activeKeyConfigured = settings.provider === "OPENROUTER"
    ? settings.providerKeys.openrouter
    : settings.providerKeys.cerebras;

  return (
    <section className="settings-page">
      <header className="section-header">
        <div>
          <span className="endpoint">RUNTIME SETTINGS</span>
          <h1>서버 설정</h1>
          <p>저장 즉시 다음 AI 요청부터 적용</p>
        </div>
        <div className="header-actions">
          <button className="button secondary" onClick={loadSettings} disabled={loading || saving}>
            <RefreshCw className={loading ? "spin" : ""} size={15} /> 다시 불러오기
          </button>
          <button className="button primary" onClick={saveSettings} disabled={saving}>
            {saving ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}
            {saving ? "저장 중" : "변경 저장"}
          </button>
        </div>
      </header>

      {(error || saved) && (
        <div className={`notice ${error ? "danger" : "success"}`}>
          {error ? <AlertCircle size={16} /> : <CheckCircle2 size={16} />}
          {error || saved}
        </div>
      )}

      <div className="settings-grid">
        <section className="settings-card model-card">
          <div className="card-heading">
            <span className="card-icon"><Sparkles size={17} /></span>
            <div><h2>AI 모델</h2><p>태스크 생성 · 사진 검증 공통</p></div>
          </div>
          <div className="model-options">
            {settings.availableModels.map((model) => {
              const active = settings.provider === model.provider && settings.model === model.id;
              const fallback = settings.fallbackModel === model.id;
              const configured = model.provider === "OPENROUTER"
                ? settings.providerKeys.openrouter
                : settings.providerKeys.cerebras;
              return (
                <div className="model-option-row" key={`${model.provider}:${model.id}`}>
                  <button className={`model-choice ${active ? "active" : ""}`} onClick={() => chooseModel(model)}>
                    <span className="radio" />
                    <span><b>{model.label.split(" · ")[0]}</b><small>{model.provider} · {model.id}</small></span>
                    <em className={configured ? "ready" : "missing"}>{configured ? "KEY OK" : "NO KEY"}</em>
                  </button>
                  {model.provider === "OPENROUTER" && (
                    <button
                      type="button"
                      className="model-remove"
                      aria-label={`${model.id} 삭제`}
                      title={active
                        ? "사용 중인 모델은 삭제할 수 없습니다."
                        : fallback
                          ? "폴백 모델은 삭제할 수 없습니다."
                          : "모델 삭제"}
                      disabled={active || fallback || settings.openrouterModels.length === 1}
                      onClick={() => removeOpenRouterModel(model.id)}
                    >
                      <Trash2 size={14} />
                    </button>
                  )}
                </div>
              );
            })}
          </div>
          <form
            className="model-add-form"
            onSubmit={(event) => {
              event.preventDefault();
              addOpenRouterModel();
            }}
          >
            <input
              aria-label="추가할 OpenRouter 모델 ID"
              placeholder="예: google/gemini-2.5-flash"
              value={newOpenRouterModel}
              maxLength={120}
              onChange={(event) => setNewOpenRouterModel(event.target.value)}
            />
            <button className="button secondary" type="submit" disabled={!newOpenRouterModel.trim()}>
              <Plus size={14} /> 추가
            </button>
          </form>
          <p className="model-help">추가·삭제 후 변경 저장을 누르세요. 폴백 모델은 운영 정책에서 별도로 선택합니다.</p>
          <p className={`key-state ${activeKeyConfigured ? "ready" : "missing"}`}>
            <span /> {activeKeyConfigured ? "활성 제공사 API 키 설정됨" : "활성 제공사 API 키가 환경변수에 없음"}
          </p>
        </section>

        <section className="settings-card cache-card">
          <div className="card-heading">
            <span className="card-icon"><RefreshCw size={17} /></span>
            <div><h2>캐시 히트</h2><p>저장된 AI 응답 재사용</p></div>
          </div>
          <label className="switch-row">
            <span>
              <b>{settings.cacheHitsEnabled ? "사용" : "사용 안 함"}</b>
              <small>OFF여도 캐시는 삭제하지 않고 조회만 건너뜁니다.</small>
            </span>
            <input
              type="checkbox"
              role="switch"
              checked={settings.cacheHitsEnabled}
              onChange={(event) => setSettings({
                ...settings,
                cacheHitsEnabled: event.target.checked,
              })}
            />
          </label>
          <label className="field compact-field">
            <span>캐시 TTL <small>1–168시간</small></span>
            <div className="unit-input">
              <input
                type="number"
                min={1}
                max={168}
                value={Math.round(settings.cacheTtlSeconds / 3600)}
                onChange={(event) => setSettings({
                  ...settings,
                  cacheTtlSeconds: Math.max(1, Math.min(168, Number(event.target.value) || 1)) * 3600,
                })}
              />
              <span>시간</span>
            </div>
          </label>
        </section>

        <section className="settings-card policy-card">
          <div className="card-heading">
            <span className="card-icon"><ShieldCheck size={17} /></span>
            <div><h2>운영 정책</h2><p>장애 대응 · 관측 기록</p></div>
          </div>
          <label className="switch-row policy-row">
            <span>
              <b>자동 장애 전환</b>
              <small>Cerebras 실패 시 선택한 OpenRouter 모델로 한 번 재시도</small>
            </span>
            <input
              type="checkbox"
              role="switch"
              checked={settings.fallbackEnabled}
              onChange={(event) => setSettings({
                ...settings,
                fallbackEnabled: event.target.checked,
              })}
            />
          </label>
          <label className="field fallback-model-field">
            <span>폴백 모델 <small>OpenRouter</small></span>
            <select
              value={settings.fallbackModel}
              onChange={(event) => setSettings({
                ...settings,
                fallbackModel: event.target.value,
              })}
            >
              {settings.openrouterModels.map((model) => (
                <option value={model} key={model}>{model}</option>
              ))}
            </select>
          </label>
          <label className="switch-row policy-row">
            <span>
              <b>요청 기록</b>
              <small>본문·사진·인증 정보 없이 요청 메타데이터 저장</small>
            </span>
            <input
              type="checkbox"
              role="switch"
              checked={settings.requestLogsEnabled}
              onChange={(event) => setSettings({
                ...settings,
                requestLogsEnabled: event.target.checked,
              })}
            />
          </label>
        </section>

        <section className="settings-card prompt-card">
          <div className="card-heading">
            <span className="card-icon code">01</span>
            <div><h2>태스크 생성 프롬프트</h2><p>POST /v1/tasks/generate</p></div>
          </div>
          <label className="field prompt-field">
            <textarea
              value={settings.prompts.taskGeneration}
              onChange={(event) => setSettings({
                ...settings,
                prompts: { ...settings.prompts, taskGeneration: event.target.value },
              })}
              maxLength={12000}
            />
            <small>{settings.prompts.taskGeneration.length.toLocaleString()} / 12,000</small>
          </label>
        </section>

        <section className="settings-card prompt-card">
          <div className="card-heading">
            <span className="card-icon code">02</span>
            <div><h2>사진 검증 프롬프트</h2><p>POST /v1/attempts/check</p></div>
          </div>
          <label className="field prompt-field">
            <textarea
              value={settings.prompts.photoCheck}
              onChange={(event) => setSettings({
                ...settings,
                prompts: { ...settings.prompts, photoCheck: event.target.value },
              })}
              maxLength={12000}
            />
            <small>{settings.prompts.photoCheck.length.toLocaleString()} / 12,000</small>
          </label>
        </section>
      </div>
    </section>
  );
}

function SettingsEmptyIcon() {
  return <span className="empty-icon"><ShieldCheck size={21} /></span>;
}
