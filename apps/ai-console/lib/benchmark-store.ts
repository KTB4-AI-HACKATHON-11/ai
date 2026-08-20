export type BenchmarkStatus = "PASS" | "RETAKE";

export type BenchmarkTask = {
  title: string;
  instruction: string;
  rule: string;
};

export type StoredPhoto = {
  blob: Blob;
  name: string;
  type: string;
  size: number;
  lastModified: number;
};

export type BenchmarkRun = {
  expectedStatus: BenchmarkStatus;
  actualStatus: BenchmarkStatus | null;
  matched: boolean;
  reason: string;
  fix: string;
  error: string;
  durationMs: number;
  ranAt: string;
  provider: string;
  model: string;
  cacheStatus: string;
  settingsRevision: number;
  usedReferencePhoto: boolean;
  initialEffectivePrompt: string;
};

export type BenchmarkRecord = {
  id: string;
  createdAt: string;
  updatedAt: string;
  task: BenchmarkTask;
  expectedStatus: BenchmarkStatus;
  photo: StoredPhoto | null;
  referencePhoto: StoredPhoto | null;
  lastRun: BenchmarkRun | null;
};

const DATABASE_NAME = "flowcheck-ai-console";
const DATABASE_VERSION = 1;
const STORE_NAME = "benchmarks";

function recordId() {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.addEventListener("success", () => resolve(request.result), { once: true });
    request.addEventListener("error", () => reject(request.error ?? new Error("브라우저 저장소 요청에 실패했습니다.")), { once: true });
  });
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.addEventListener("complete", () => resolve(), { once: true });
    transaction.addEventListener("abort", () => reject(transaction.error ?? new Error("브라우저 저장소 작업이 취소되었습니다.")), { once: true });
    transaction.addEventListener("error", () => reject(transaction.error ?? new Error("브라우저 저장소 작업에 실패했습니다.")), { once: true });
  });
}

async function openDatabase(): Promise<IDBDatabase> {
  if (typeof indexedDB === "undefined") {
    throw new Error("이 브라우저에서는 벤치마크 저장소를 사용할 수 없습니다.");
  }
  const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
  request.addEventListener("upgradeneeded", () => {
    const database = request.result;
    if (!database.objectStoreNames.contains(STORE_NAME)) {
      database.createObjectStore(STORE_NAME, { keyPath: "id" });
    }
  }, { once: true });
  return requestResult(request);
}

export function storedPhoto(file: File): StoredPhoto {
  return {
    blob: file,
    name: file.name,
    type: file.type,
    size: file.size,
    lastModified: file.lastModified,
  };
}

export function photoFile(photo: StoredPhoto): File {
  return new File([photo.blob], photo.name, {
    type: photo.type,
    lastModified: photo.lastModified,
  });
}

export async function listBenchmarkRecords(): Promise<BenchmarkRecord[]> {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(STORE_NAME, "readonly");
    const records = await requestResult(transaction.objectStore(STORE_NAME).getAll()) as BenchmarkRecord[];
    await transactionDone(transaction);
    return records.sort((left, right) => left.createdAt.localeCompare(right.createdAt));
  } finally {
    database.close();
  }
}

export async function addBenchmarkRecord(task: BenchmarkTask): Promise<BenchmarkRecord> {
  const now = new Date().toISOString();
  const record: BenchmarkRecord = {
    id: recordId(),
    createdAt: now,
    updatedAt: now,
    task,
    expectedStatus: "PASS",
    photo: null,
    referencePhoto: null,
    lastRun: null,
  };
  await putBenchmarkRecord(record);
  return record;
}

export async function putBenchmarkRecord(record: BenchmarkRecord): Promise<void> {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).put(record);
    await transactionDone(transaction);
  } finally {
    database.close();
  }
}

export async function deleteBenchmarkRecord(id: string): Promise<void> {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).delete(id);
    await transactionDone(transaction);
  } finally {
    database.close();
  }
}
