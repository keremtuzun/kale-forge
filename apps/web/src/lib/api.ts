// Typed client for the Kale Forge analysis service. Every call in the architecture API table
// is represented here. Base URL from NEXT_PUBLIC_ANALYSIS_API_URL.
import type {
  AIReview,
  ChatResponse,
  FindingsResponse,
  ModelVersion,
  NormalizedProject,
  PowerTree,
  ProjectSummary,
} from "@kale/shared-types";

// The all-in-one hosted build proxies /api to the analysis service. Normal local and
// multi-container builds keep using the explicitly configured analysis URL.
const BASE = process.env.NEXT_PUBLIC_USE_SAME_ORIGIN === "true"
  ? ""
  : (process.env.NEXT_PUBLIC_ANALYSIS_API_URL || "http://localhost:8000");

function adminHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const token = window.sessionStorage.getItem("kale_admin_token");
  return token ? { "X-Admin-Token": token } : {};
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}/api${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...adminHeaders(), ...(init?.headers || {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text.slice(0, 200)}`);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

export const api = {
  base: BASE,
  listProjects: () => req<ProjectSummary[]>("/projects"),
  createProject: (name: string, description = "") =>
    req<ProjectSummary>("/projects", { method: "POST", body: JSON.stringify({ name, description }) }),
  getProject: (id: string) => req<ProjectSummary>(`/projects/${id}`),
  deleteProject: (id: string) => req<void>(`/projects/${id}`, { method: "DELETE" }),

  uploadFiles: async (id: string, files: FileList | File[]) => {
    const form = new FormData();
    Array.from(files).forEach((f) => form.append("files", f));
    const res = await fetch(`${BASE}/api/projects/${id}/files`, { method: "POST", body: form });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(body?.detail || `Upload failed (${res.status})`);
    }
    return res.json();
  },

  analyze: (id: string) => req<{ id: string; status: string }>(`/projects/${id}/analyze`, { method: "POST" }),
  importProjectObjToOnshape: (id: string) => req<{ url: string; translation: string }>(`/projects/${id}/onshape/import`, { method: "POST" }),
  jobStatus: (id: string, jobId: string) =>
    req<{ id: string; status: string; error: string }>(`/projects/${id}/jobs/${jobId}`),
  getData: (id: string) =>
    req<{ normalized: NormalizedProject; power_tree: PowerTree | null; model_version: string; rule_engine_version: string }>(
      `/projects/${id}/data`,
    ),
  getFindings: (id: string) => req<FindingsResponse>(`/projects/${id}/findings`),
  getPower: (id: string) =>
    req<{ power_tree: PowerTree | null; current_overrides: Record<string, number> }>(`/projects/${id}/power`),
  updatePower: (id: string, overrides: Record<string, number>) =>
    req<{ power_tree: PowerTree | null; current_overrides: Record<string, number> }>(`/projects/${id}/power`, {
      method: "PATCH",
      body: JSON.stringify({ overrides }),
    }),
  chat: (id: string, question: string, conversationId?: string) =>
    req<ChatResponse>(`/projects/${id}/chat`, {
      method: "POST",
      body: JSON.stringify({ question, conversation_id: conversationId ?? null }),
    }),
  submitFeedback: (findingId: string, body: Record<string, unknown>) =>
    req<{ id: string }>(`/findings/${findingId}/feedback`, { method: "POST", body: JSON.stringify(body) }),
  updateComponent: (componentId: string, body: Record<string, unknown>) =>
    req<unknown>(`/components/${componentId}`, { method: "PATCH", body: JSON.stringify(body) }),
  report: (id: string) => req<string>(`/projects/${id}/report`, { method: "POST" }),
  listModels: () => req<ModelVersion[]>("/models"),
  selectModel: (version: string) =>
    req<{ active: string }>("/models/select", { method: "POST", body: JSON.stringify({ version }) }),
  evaluations: () => req<{ db_runs: unknown[]; file_reports: unknown[] }>("/evaluations"),
  me: () => req<KaleUser>("/auth/me"),
  register: (name: string, email: string, password: string) => req<KaleUser>("/auth/register", {
    method: "POST", body: JSON.stringify({ name, email, password }),
  }),
  login: (email: string, password: string) => req<KaleUser>("/auth/login", {
    method: "POST", body: JSON.stringify({ email, password }),
  }),
  forgotPassword: (email: string) => req<{ message: string }>("/auth/forgot-password", {
    method: "POST", body: JSON.stringify({ email }),
  }),
  resetPassword: (token: string, password: string) => req<{ message: string }>("/auth/reset-password", {
    method: "POST", body: JSON.stringify({ token, password }),
  }),
  logout: () => req<void>("/auth/logout", { method: "POST" }),
  updateProfile: (name: string) => req<KaleUser>("/auth/me", { method: "PATCH", body: JSON.stringify({ name }) }),
  listDesigns: () => req<DesignRecord[]>("/designs"),
  listSeasons: () => req<{ seasons: SeasonOption[]; default: string }>("/designs/seasons"),
  createDesign: (kind: "pcb" | "robot", prompt: string, name = "", season = "") =>
    req<DesignRecord>("/designs", { method: "POST", body: JSON.stringify({ kind, prompt, name, season }) }),
  editDesign: (id: string, prompt: string) =>
    req<DesignRecord>(`/designs/${id}/edit`, { method: "POST", body: JSON.stringify({ prompt }) }),
  deleteDesign: (id: string) => req<void>(`/designs/${id}`, { method: "DELETE" }),
  loadWorkedExample: () => req<DesignRecord>("/designs/example", { method: "POST" }),
  artifactUrl: (id: string, filename: string) => `${BASE}/api/designs/${id}/files/${encodeURIComponent(filename)}`,
  onshapeStatus: () => req<{ connected: boolean; user?: string; storage: string }>("/designs/onshape/status"),
  connectOnshape: (accessKey: string, secretKey: string) =>
    req<{ connected: boolean; user: string }>("/designs/onshape/connect", {
      method: "POST", body: JSON.stringify({ access_key: accessKey, secret_key: secretKey }),
    }),
  disconnectOnshape: () => req<void>("/designs/onshape/connect", { method: "DELETE" }),
  publishOnshape: (id: string) =>
    req<{ url: string; translation: string; document_id: string; workspace_id: string; mode: string }>(`/designs/${id}/onshape/publish`, { method: "POST" }),
  copySerpentheim: (name = "2025 Serpentheim — Editable Copy") => req<DesignRecord>("/designs/templates/serpentheim/copy", {
    method: "POST", body: JSON.stringify({ name }),
  }),
};

export type { AIReview };

export interface SeasonOption {
  key: string;
  label: string;
  game: string;
  year: number;
  summary: string;
  gamepiece: string;
  frame_in: [number, number];
  perimeter_in: number;
  default_subsystems: string[];
}

export interface DesignRecord {
  id: string;
  kind: "pcb" | "robot";
  season?: string;
  name: string;
  status: string;
  revision: number;
  prompt: string;
  last_edit?: string;
  spec: Record<string, any>;
  artifacts: string[];
  warnings: string[];
  created_at: string;
  updated_at: string;
  is_example?: boolean;
  example_note?: string;
  onshape?: { url: string; document_id: string; workspace_id: string; published_revision: number; mode?: string };
}

export interface KaleUser { id: string; email: string; name: string; is_admin: boolean; created_at: string; }
