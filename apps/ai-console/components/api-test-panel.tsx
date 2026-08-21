"use client";

import Image from "next/image";
import {
  AlertCircle,
  Camera,
  Check,
  CheckCircle2,
  Gauge,
  ImagePlus,
  LoaderCircle,
  MessageCircleQuestion,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  XCircle,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { apiError, requireConnection, responseJson, type Connection } from "@/lib/api";
import type { BenchmarkTask } from "@/lib/benchmark-store";

type CompletionType = "PHOTO" | "CHECK";
type VerificationResult =
  | { status: "PASS"; reason: string }
  | { status: "RETAKE"; reason: string; fix: string };

type EditableTask = {
  clientId: string;
  title: string;
  instruction: string;
  completionType: CompletionType;
  rule: string | null;
  photo: File | null;
  previewUrl: string;
  referencePhoto: File | null;
  referencePreviewUrl: string;
  result: VerificationResult | null;
  checking: boolean;
  error: string;
};

const DEFAULT_PROMPT = "오픈 전에 조명을 켜고 POS기 전원과 카운터 정리를 확인해야 해";
const DEFAULT_INFORMATION = `
[매장 기본 운영]
1. 매장 영업시간은 매일 오전 6시부터 다음 날 오전 1시까지다.
2. 오픈 담당자는 오전 5시 40분까지 출근하고 출입문, 간판, 실내 조명을 차례로 켠다.
3. 오픈 담당자는 영업 시작 전에 입구와 계산대 주변 바닥의 청소 상태를 확인한다.
4. 오픈 시재는 현금 30만 원이며, 금액이 다르면 영업 시작 전에 점장에게 보고한다.
5. 영수증 용지는 계산대 아래 두 번째 서랍에 보관하며 예비 용지를 최소 3롤 유지한다.
6. 마감 담당자는 오전 12시 40분부터 외부 진열 상품을 안으로 이동한다.
7. 마지막 고객이 퇴점한 뒤 출입문을 잠그고 간판, 실내 조명, 냉난방기 순서로 끈다.
8. 냉장고 적정 온도는 1~5도이며 6도 이상이면 점장에게 즉시 연락한다.
9. 냉동고 적정 온도는 영하 18도 이하이며 성에가 심하면 임의로 제거하지 말고 시설 점검을 요청한다.
10. 근무 중 개인 휴대전화는 휴게실 보관함에 두되 점장과 긴급 연락을 받을 수 있도록 벨은 켜 둔다.

[2026년 8월 행사 및 할인]
11. 제로 탄산음료 500ml 2+1 행사는 8월 21일 00시부터 8월 31일 23시 59분까지 진행한다.
12. 제로 탄산음료 2+1은 행사 라벨이 붙은 같은 용량 상품끼리만 적용한다.
13. 미네랄워터 500ml는 8월 20일부터 8월 27일까지 두 개를 1,800원에 판매한다.
14. 아메리카노 300ml는 오전 6시부터 오전 10시까지 500원 할인한다.
15. 아메리카노 아침 할인은 다른 모바일 쿠폰과 중복 적용하지 않는다.
16. 참치마요 삼각김밥과 아메리카노를 함께 구매하면 합계 금액에서 700원을 할인한다.
17. 삼각김밥·커피 세트 할인은 8월 24일부터 8월 30일까지 적용한다.
18. 체크온 멤버십 고객은 8월 한 달 동안 제육 도시락 구매 시 생수 500ml 한 병을 증정받는다.
19. 멤버십 증정 행사는 결제 전에 멤버십 바코드를 스캔해야 적용된다.
20. 체크온페이로 2만 원 이상 결제하면 8월 28일부터 8월 31일까지 결제 금액의 10%를 즉시 할인한다.

[상품 가격과 위치]
21. 제로 탄산음료 500ml 정상 가격은 2,200원이며 음료 매대 1번 통로 2번 선반 2단에 있다.
22. 미네랄워터 500ml 정상 가격은 1,100원이며 음료 매대 1번 통로 1번 선반 1단에 있다.
23. 아메리카노 300ml 정상 가격은 2,500원이며 워크인 냉장고 앞쪽 커피 칸에 있다.
24. 에너지 드링크 250ml 가격은 2,400원이며 음료 매대 1번 통로 3번 선반 2단에 있다.
25. 신선 우유 900ml 가격은 3,200원이며 워크인 냉장고 오른쪽 1번 선반 아래 칸에 있다.
26. 플레인 요구르트 150ml 가격은 1,800원이며 워크인 냉장고 오른쪽 2번 선반 가운데 칸에 있다.
27. 제육 도시락 가격은 5,900원이며 냉장 간편식 매대 맨 위 칸에 있다.
28. 참치마요 삼각김밥 가격은 1,400원이며 냉장 간편식 매대 두 번째 칸에 있다.
29. 불고기 김밥 가격은 3,200원이며 냉장 간편식 매대 세 번째 칸에 있다.
30. 햄치즈 샌드위치 가격은 3,500원이며 냉장 간편식 매대 왼쪽 아래 칸에 있다.
31. 감자 스낵 80g 가격은 1,800원이며 과자 매대 3번 통로 1번 선반 2단에 있다.
32. 밀크 초콜릿 50g 가격은 2,000원이며 계산대 앞 소형 진열대 오른쪽 칸에 있다.
33. 매운 컵라면 큰컵 가격은 1,900원이며 라면 매대 4번 통로 2번 선반 맨 위 칸에 있다.
34. KF94 마스크 가격은 1매당 1,000원이며 계산대 뒤 위생용품 서랍에 있다.
35. 여행용 칫솔세트 가격은 2,800원이며 생활용품 매대 5번 통로 3번 선반 2단에 있다.

[현재 재고와 입고]
36. 8월 20일 오후 3시 기준 제육 도시락 판매 가능 재고는 4개이고 안전재고는 3개다.
37. 8월 20일 오후 3시 기준 참치마요 삼각김밥 판매 가능 재고는 12개다.
38. 신선 우유는 매장에 3개, 후면 창고에 8개 있으며 가장 가까운 소비기한은 8월 25일이다.
39. 제로 탄산음료는 매장에 15개, 후면 창고에 24개 있어 행사 진열대가 비면 창고에서 보충한다.
40. KF94 마스크는 판매 가능 재고가 2개뿐이므로 추가 발주 전까지 한 고객당 5매까지만 판매한다.
41. 매일 오전 8시 음료와 유제품이 입고되고, 오후 2시 도시락과 삼각김밥이 입고된다.
42. 입고 상품은 납품서와 박스 수량을 대조한 뒤 파손, 냉장 상태, 소비기한 순서로 확인한다.
43. 냉장 상품 온도가 10도를 넘거나 포장이 뜯겨 있으면 입고 완료 처리하지 않고 점장에게 보고한다.
44. 소비기한이 24시간 이내인 도시락과 김밥은 할인 스티커를 붙이고 별도 칸에 진열한다.
45. 소비기한이 지난 상품은 POS에서 폐기 등록한 후 폐기 바구니에 분리하고 담당자 확인을 받는다.

[고객 서비스]
46. 일반 택배 접수는 오후 9시에 마감하며 냉장·냉동 상품은 접수하지 않는다.
47. 택배 접수 후 운송장은 고객용과 매장 보관용으로 나누고 매장 보관용은 날짜별 파일에 넣는다.
48. 환불은 영수증, 결제수단, 상품 상태를 확인한 뒤 POS 환불 메뉴로 처리한다.
49. 영수증이 없거나 일부 사용한 상품의 환불은 알바생이 단독 처리하지 않고 점장 승인을 받는다.
50. 분실물은 발견 시간과 위치를 기록하고 분실물 봉투에 넣어 카운터 하단 잠금 보관함에 둔다.

[안전 및 장비]
51. 손님이 쓰러지면 즉시 119에 신고하고 점장에게 연락한 뒤 응급요원의 안내를 따른다.
52. 화재경보가 울리면 계산대 옆 비상 버튼으로 출입문을 개방하고 고객을 건물 밖 집결지로 안내한다.
53. 매장 집결지는 건물 정면 기준 오른쪽 공영주차장 입구다.
54. 강도나 위협 상황에서는 물리적으로 대응하지 말고 안전을 확보한 뒤 비상 버튼과 112를 이용한다.
55. POS가 멈추면 진행 중인 결제가 없는지 확인하고 관리자 메뉴의 시스템 재시작을 사용한다.
56. POS 전원 코드는 알바생이 임의로 뽑지 않으며 재시작으로 해결되지 않으면 점장에게 연락한다.
57. 커피 머신 마감 청소는 대기 상태 전환, 세척 메뉴 실행, 노즐과 물받이 분리 세척 순서로 한다.
58. 화장실 청소 중에는 입구에 청소 안내판을 세우고 세제 사용 후 바닥에 물기가 남지 않게 닦는다.
59. 점장 연락이 필요한 일반 업무 문제는 매장 단체 채팅의 '운영 문의' 방에 사진과 함께 남긴다.
60. 안전사고, 냉장·냉동 온도 이상, 현금 시재 불일치는 시간과 관계없이 점장에게 전화로 즉시 보고한다.
`.trim();
const DEFAULT_QUESTION = "일반 택배는 몇 시까지 받고 냉장 택배도 접수할 수 있어?";
const PHOTO_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

function taskId() {
  const webCrypto = globalThis.crypto;
  if (typeof webCrypto?.randomUUID === "function") {
    return webCrypto.randomUUID();
  }
  if (typeof webCrypto?.getRandomValues === "function") {
    const bytes = webCrypto.getRandomValues(new Uint8Array(16));
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function editableTask(value: unknown): EditableTask | null {
  if (!value || typeof value !== "object") return null;
  const task = value as Record<string, unknown>;
  const completionType = task.completionType;
  const validRule = completionType === "PHOTO" ? typeof task.rule === "string" : task.rule === null;
  if (
    typeof task.title !== "string" ||
    typeof task.instruction !== "string" ||
    (completionType !== "PHOTO" && completionType !== "CHECK") ||
    !validRule
  ) return null;
  return {
    clientId: taskId(),
    title: task.title,
    instruction: task.instruction,
    completionType,
    rule: completionType === "PHOTO" ? String(task.rule) : null,
    photo: null,
    previewUrl: "",
    referencePhoto: null,
    referencePreviewUrl: "",
    result: null,
    checking: false,
    error: "",
  };
}

export function ApiTestPanel({
  connection,
  onAddBenchmark,
}: {
  connection: Connection;
  onAddBenchmark: (task: BenchmarkTask) => Promise<void>;
}) {
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [tasks, setTasks] = useState<EditableTask[]>([]);
  const [generating, setGenerating] = useState(false);
  const [generationError, setGenerationError] = useState("");
  const [information, setInformation] = useState(DEFAULT_INFORMATION);
  const [knowledgeQuestion, setKnowledgeQuestion] = useState(DEFAULT_QUESTION);
  const [knowledgeAnswer, setKnowledgeAnswer] = useState("");
  const [knowledgeError, setKnowledgeError] = useState("");
  const [answeringKnowledge, setAnsweringKnowledge] = useState(false);
  const [benchmarkAddedIds, setBenchmarkAddedIds] = useState<Set<string>>(new Set());
  const previewUrls = useRef(new Set<string>());

  useEffect(() => {
    const urls = previewUrls.current;
    return () => urls.forEach((url) => URL.revokeObjectURL(url));
  }, []);

  function releasePreviewUrl(url: string) {
    if (!url) return;
    URL.revokeObjectURL(url);
    previewUrls.current.delete(url);
  }

  function releasePreviews(task: EditableTask) {
    releasePreviewUrl(task.previewUrl);
    releasePreviewUrl(task.referencePreviewUrl);
  }

  function updateTask(clientId: string, patch: Partial<EditableTask>) {
    setTasks((current) => current.map((task) => task.clientId === clientId
      ? { ...task, ...patch, result: patch.result ?? null, error: patch.error ?? "" }
      : task));
  }

  async function generateTasks() {
    setGenerationError("");
    if (!prompt.trim()) {
      setGenerationError("해야 할 일을 입력해 주세요.");
      return;
    }
    setGenerating(true);
    try {
      const { baseUrl } = requireConnection(connection);
      const response = await fetch(`${baseUrl}/v1/tasks/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: prompt.trim() }),
      });
      const data = await responseJson(response);
      if (!response.ok) throw new Error(apiError(data, response.status));
      const rawTasks = (data as { tasks?: unknown[] } | null)?.tasks;
      const parsed = Array.isArray(rawTasks) ? rawTasks.map(editableTask) : [];
      if (!parsed.length || parsed.some((task) => task === null)) {
        throw new Error("태스크 응답 형식이 계약과 다릅니다.");
      }
      tasks.forEach(releasePreviews);
      setTasks(parsed as EditableTask[]);
    } catch (caught) {
      setGenerationError(caught instanceof Error ? caught.message : "태스크 생성 실패");
    } finally {
      setGenerating(false);
    }
  }

  async function answerKnowledge() {
    setKnowledgeError("");
    setKnowledgeAnswer("");
    if (!information.trim() || !knowledgeQuestion.trim()) {
      setKnowledgeError("정보와 질문을 모두 입력해 주세요.");
      return;
    }
    setAnsweringKnowledge(true);
    try {
      const { baseUrl } = requireConnection(connection);
      const response = await fetch(`${baseUrl}/v1/knowledge/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          information: information.trim(),
          question: knowledgeQuestion.trim(),
        }),
      });
      const data = await responseJson(response);
      if (!response.ok) throw new Error(apiError(data, response.status));
      const answer = (data as { answer?: unknown } | null)?.answer;
      if (typeof answer !== "string" || !answer.trim()) {
        throw new Error("정보 답변 형식이 계약과 다릅니다.");
      }
      setKnowledgeAnswer(answer);
    } catch (caught) {
      setKnowledgeError(caught instanceof Error ? caught.message : "정보 답변 실패");
    } finally {
      setAnsweringKnowledge(false);
    }
  }

  function addTask() {
    setTasks((current) => [...current, {
      clientId: taskId(),
      title: "새 사진 태스크",
      instruction: "확인할 대상이 잘 보이도록 촬영해 주세요.",
      completionType: "PHOTO",
      rule: "사진에서 완료 상태가 명확히 보여야 한다.",
      photo: null,
      previewUrl: "",
      referencePhoto: null,
      referencePreviewUrl: "",
      result: null,
      checking: false,
      error: "",
    }]);
  }

  function removeTask(task: EditableTask) {
    releasePreviews(task);
    setTasks((current) => current.filter((item) => item.clientId !== task.clientId));
  }

  function changeCompletionType(task: EditableTask, completionType: CompletionType) {
    if (completionType === "CHECK") releasePreviews(task);
    updateTask(task.clientId, {
      completionType,
      rule: completionType === "PHOTO" ? task.rule || "사진에서 완료 상태가 명확히 보여야 한다." : null,
      photo: completionType === "PHOTO" ? task.photo : null,
      previewUrl: completionType === "PHOTO" ? task.previewUrl : "",
      referencePhoto: completionType === "PHOTO" ? task.referencePhoto : null,
      referencePreviewUrl: completionType === "PHOTO" ? task.referencePreviewUrl : "",
    });
  }

  function choosePhoto(task: EditableTask, target: "photo" | "referencePhoto", file: File | null) {
    if (!file) return;
    if (!PHOTO_TYPES.has(file.type)) {
      updateTask(task.clientId, { error: "JPEG, PNG 또는 WebP만 가능합니다." });
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      updateTask(task.clientId, { error: "사진은 10MB 이하여야 합니다." });
      return;
    }
    releasePreviewUrl(target === "photo" ? task.previewUrl : task.referencePreviewUrl);
    const previewUrl = URL.createObjectURL(file);
    previewUrls.current.add(previewUrl);
    updateTask(task.clientId, target === "photo"
      ? { photo: file, previewUrl }
      : { referencePhoto: file, referencePreviewUrl: previewUrl });
  }

  async function verifyTask(task: EditableTask) {
    updateTask(task.clientId, { checking: true });
    try {
      const { baseUrl } = requireConnection(connection);
      if (task.completionType !== "PHOTO") throw new Error("PHOTO 태스크만 검증할 수 있습니다.");
      if (!task.title.trim() || !task.instruction.trim() || !task.rule?.trim()) {
        throw new Error("이름, 안내와 Rule을 모두 입력해 주세요.");
      }
      if (!task.photo) throw new Error("인증 사진을 선택해 주세요.");
      const form = new FormData();
      form.append("task", JSON.stringify({
        title: task.title.trim(),
        instruction: task.instruction.trim(),
        rule: task.rule.trim(),
      }));
      form.append("photo", task.photo, task.photo.name);
      if (task.referencePhoto) form.append("referencePhoto", task.referencePhoto, task.referencePhoto.name);
      const response = await fetch(`${baseUrl}/v1/attempts/check`, {
        method: "POST",
        body: form,
      });
      const data = await responseJson(response);
      if (!response.ok) throw new Error(apiError(data, response.status));
      const result = data as Partial<VerificationResult> | null;
      if (
        !result ||
        (result.status !== "PASS" && result.status !== "RETAKE") ||
        typeof result.reason !== "string" ||
        (result.status === "RETAKE" && typeof result.fix !== "string")
      ) throw new Error("검증 응답 형식이 계약과 다릅니다.");
      updateTask(task.clientId, { result: result as VerificationResult, checking: false });
    } catch (caught) {
      updateTask(task.clientId, {
        checking: false,
        error: caught instanceof Error ? caught.message : "사진 검증 실패",
      });
    }
  }

  async function addToBenchmark(task: EditableTask) {
    if (task.completionType !== "PHOTO" || !task.title.trim() || !task.instruction.trim() || !task.rule?.trim()) {
      updateTask(task.clientId, { error: "PHOTO 태스크의 이름, 안내와 Rule을 모두 입력해 주세요." });
      return;
    }
    try {
      await onAddBenchmark({
        title: task.title.trim(),
        instruction: task.instruction.trim(),
        rule: task.rule.trim(),
      });
      setBenchmarkAddedIds((current) => new Set(current).add(task.clientId));
    } catch (caught) {
      updateTask(task.clientId, {
        error: caught instanceof Error ? caught.message : "벤치마크에 추가하지 못했습니다.",
      });
    }
  }

  const photoTasks = tasks.filter((task) => task.completionType === "PHOTO").length;
  const passed = tasks.filter((task) => task.result?.status === "PASS").length;

  return (
    <section className="test-page">
      <header className="section-header compact">
        <div>
          <span className="endpoint">API TEST</span>
          <h1>요청 테스트</h1>
        </div>
        <div className="metrics">
          <span><b>{tasks.length}</b> 태스크</span>
          <span><b>{photoTasks}</b> 사진</span>
          <span className={passed ? "pass" : ""}><b>{passed}</b> 통과</span>
        </div>
      </header>

      <div className="test-grid">
        <section className="task-column">
          <div className="column-heading">
            <div><span className="endpoint">POST /v1/attempts/check</span><h2>태스크 목록</h2></div>
            <button className="button secondary" onClick={addTask}><Plus size={15} /> 추가</button>
          </div>
          {tasks.length === 0 ? (
            <div className="task-empty"><strong>태스크 없음</strong><p>오른쪽에서 생성하거나 직접 추가하세요.</p><button className="button secondary" onClick={addTask}><Plus size={15} /> 직접 추가</button></div>
          ) : (
            <div className="task-list">
              {tasks.map((task, index) => (
                <article className={`task-card ${task.result?.status?.toLowerCase() || ""}`} key={task.clientId}>
                  <header className="task-head">
                    <span className="task-index">{String(index + 1).padStart(2, "0")}</span>
                    <div className="task-type">
                      <button className={task.completionType === "PHOTO" ? "active" : ""} onClick={() => changeCompletionType(task, "PHOTO")}><Camera size={13} /> PHOTO</button>
                      <button className={task.completionType === "CHECK" ? "active" : ""} onClick={() => changeCompletionType(task, "CHECK")}><Check size={13} /> CHECK</button>
                    </div>
                    {task.completionType === "PHOTO" && (
                      <button className={`task-benchmark-button ${benchmarkAddedIds.has(task.clientId) ? "added" : ""}`} onClick={() => addToBenchmark(task)}>
                        {benchmarkAddedIds.has(task.clientId) ? <Check size={12} /> : <Gauge size={12} />}
                        {benchmarkAddedIds.has(task.clientId) ? "추가됨" : "벤치마크 추가"}
                      </button>
                    )}
                    <button className="icon-button remove" onClick={() => removeTask(task)} aria-label={`${task.title} 삭제`}><Trash2 size={15} /></button>
                  </header>
                  <div className="task-fields">
                    <label className="field"><span>태스크 이름</span><input value={task.title} maxLength={80} onChange={(event) => updateTask(task.clientId, { title: event.target.value })} /></label>
                    <label className="field"><span>수행 안내</span><textarea value={task.instruction} maxLength={500} onChange={(event) => updateTask(task.clientId, { instruction: event.target.value })} /></label>
                    {task.completionType === "PHOTO" && <label className="field rule"><span>Rule <small>통과 조건</small></span><textarea value={task.rule || ""} maxLength={1000} onChange={(event) => updateTask(task.clientId, { rule: event.target.value })} /></label>}
                  </div>

                  {task.completionType === "PHOTO" ? (
                    <div className="verification-zone">
                      <div className="photo-slots">
                        <PhotoSlot label="인증 사진" required previewUrl={task.previewUrl} taskTitle={task.title} onChange={(file) => choosePhoto(task, "photo", file)} />
                        <PhotoSlot label="모범 사진" previewUrl={task.referencePreviewUrl} taskTitle={task.title} onChange={(file) => choosePhoto(task, "referencePhoto", file)} />
                      </div>
                      <div className="verify-side">
                        {task.photo && <FileLine label="인증" file={task.photo} />}
                        {task.referencePhoto && <FileLine label="모범" file={task.referencePhoto} />}
                        <button className="button verify" onClick={() => verifyTask(task)} disabled={task.checking}>
                          {task.checking ? <LoaderCircle className="spin" size={16} /> : <ShieldCheck size={16} />}
                          {task.checking ? "검증 중" : task.referencePhoto ? "모범 사진과 비교" : "사진 검증"}
                        </button>
                        {!task.photo && <small className="hint">인증 사진 필요</small>}
                      </div>
                    </div>
                  ) : <div className="check-only"><CheckCircle2 size={17} /><span><b>직접 완료</b><small>AI 사진 검증 없음</small></span></div>}

                  {task.error && <div className="notice danger task-notice"><AlertCircle size={15} /> {task.error}</div>}
                  {task.result && (
                    <div className={`result ${task.result.status.toLowerCase()}`}>
                      {task.result.status === "PASS" ? <CheckCircle2 size={19} /> : <XCircle size={19} />}
                      <div><b>{task.result.status}</b><p>{task.result.reason}</p>{task.result.status === "RETAKE" && <small><RefreshCw size={12} /> {task.result.fix}</small>}</div>
                    </div>
                  )}
                </article>
              ))}
            </div>
          )}
        </section>

        <aside className="generate-card">
          <span className="endpoint">POST /v1/tasks/generate</span>
          <h2>태스크 생성</h2>
          <p>자연어 입력</p>
          <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} maxLength={2000} />
          <div className="prompt-footer"><span>{prompt.length} / 2,000</span><button className="button primary" onClick={generateTasks} disabled={generating}>{generating ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}{generating ? "생성 중" : "생성"}</button></div>
          {generationError && <div className="notice danger"><AlertCircle size={15} /> {generationError}</div>}
        </aside>
      </div>

      <section className="knowledge-card">
        <header className="knowledge-heading">
          <div>
            <span className="endpoint">POST /v1/knowledge/answer</span>
            <h2>매장 정보 질문</h2>
            <p>사장이 기록한 정보만 근거로 답하며, 이 요청은 캐시하지 않습니다.</p>
          </div>
          <MessageCircleQuestion size={24} aria-hidden="true" />
        </header>
        <div className="knowledge-grid">
          <label className="field knowledge-information">
            <span>알바생이 알아야 할 정보 <small>{information.length.toLocaleString()} / 60,000</small></span>
            <textarea disabled={answeringKnowledge} value={information} maxLength={60000} onChange={(event) => setInformation(event.target.value)} />
          </label>
          <div className="knowledge-question-side">
            <label className="field">
              <span>질문 <small>{knowledgeQuestion.length.toLocaleString()} / 10,000</small></span>
              <textarea disabled={answeringKnowledge} value={knowledgeQuestion} maxLength={10000} onChange={(event) => setKnowledgeQuestion(event.target.value)} />
            </label>
            <button className="button primary" onClick={answerKnowledge} disabled={answeringKnowledge}>
              {answeringKnowledge ? <LoaderCircle className="spin" size={16} /> : <MessageCircleQuestion size={16} />}
              {answeringKnowledge ? "답변 중" : "질문하기"}
            </button>
            {knowledgeError && <div className="notice danger" role="alert"><AlertCircle size={15} /> {knowledgeError}</div>}
            {knowledgeAnswer && <div className="knowledge-answer" aria-live="polite"><span>답변</span><p>{knowledgeAnswer}</p></div>}
          </div>
        </div>
      </section>
    </section>
  );
}

function PhotoSlot({
  label,
  required = false,
  previewUrl,
  taskTitle,
  onChange,
}: {
  label: string;
  required?: boolean;
  previewUrl: string;
  taskTitle: string;
  onChange: (file: File | null) => void;
}) {
  return (
    <div className="photo-slot">
      <span>{label}<small>{required ? "필수" : "선택"}</small></span>
      <label className={`photo-drop ${previewUrl ? "has-photo" : ""}`}>
        <input className="sr-only" type="file" accept="image/jpeg,image/png,image/webp" onChange={(event) => onChange(event.target.files?.[0] || null)} />
        {previewUrl ? <><Image src={previewUrl} alt={`${taskTitle} ${label}`} fill sizes="130px" style={{ objectFit: "cover" }} unoptimized /><em><RefreshCw size={11} /> 변경</em></> : <><i><ImagePlus size={17} /></i><b>선택</b><small>10MB 이하</small></>}
      </label>
    </div>
  );
}

function FileLine({ label, file }: { label: string; file: File }) {
  return <div className="file-line"><Upload size={13} /><b>{label}</b><span>{file.name}</span><small>{Math.ceil(file.size / 1024)}KB</small></div>;
}
