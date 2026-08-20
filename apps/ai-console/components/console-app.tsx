"use client";

import { FlaskConical, Gauge, History, ServerCog, Settings2 } from "lucide-react";
import { useState } from "react";

import { ApiTestPanel } from "@/components/api-test-panel";
import { BenchmarkPanel } from "@/components/benchmark-panel";
import { RequestLogsPanel } from "@/components/request-logs-panel";
import { SettingsPanel } from "@/components/settings-panel";
import type { Connection } from "@/lib/api";
import { addBenchmarkRecord, type BenchmarkTask } from "@/lib/benchmark-store";

type Tab = "test" | "benchmark" | "logs" | "settings";
const SERVER_CONNECTION: Connection = {
  backendUrl: "/api/ai",
  authKey: "server-managed",
};

export function ConsoleApp() {
  const [tab, setTab] = useState<Tab>("test");
  const [benchmarkRevision, setBenchmarkRevision] = useState(0);

  async function addToBenchmark(task: BenchmarkTask) {
    await addBenchmarkRecord(task);
    setBenchmarkRevision((value) => value + 1);
  }

  return (
    <main className="console-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark"><ServerCog size={18} /></span>
          <span><b>AI BACKEND</b><small>CONSOLE</small></span>
        </div>
        <nav className="tabs" aria-label="콘솔 메뉴" role="tablist">
          <button role="tab" aria-selected={tab === "test"} className={tab === "test" ? "active" : ""} onClick={() => setTab("test")}>
            <FlaskConical size={15} /> API 테스트
          </button>
          <button role="tab" aria-selected={tab === "benchmark"} className={tab === "benchmark" ? "active" : ""} onClick={() => setTab("benchmark")}>
            <Gauge size={15} /> 벤치마크
          </button>
          <button role="tab" aria-selected={tab === "logs"} className={tab === "logs" ? "active" : ""} onClick={() => setTab("logs")}>
            <History size={15} /> 요청 기록
          </button>
          <button role="tab" aria-selected={tab === "settings"} className={tab === "settings" ? "active" : ""} onClick={() => setTab("settings")}>
            <Settings2 size={15} /> 서버 설정
          </button>
        </nav>
      </header>

      <div className="console-content">
        <div role="tabpanel" hidden={tab !== "test"}>
          <ApiTestPanel connection={SERVER_CONNECTION} onAddBenchmark={addToBenchmark} />
        </div>
        <div role="tabpanel" hidden={tab !== "benchmark"}>
          <BenchmarkPanel connection={SERVER_CONNECTION} revision={benchmarkRevision} />
        </div>
        <div role="tabpanel" hidden={tab !== "logs"}>
          <RequestLogsPanel connection={SERVER_CONNECTION} />
        </div>
        <div role="tabpanel" hidden={tab !== "settings"}>
          <SettingsPanel connection={SERVER_CONNECTION} />
        </div>
      </div>
    </main>
  );
}
