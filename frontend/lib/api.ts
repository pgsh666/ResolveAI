import { serviceDisplayText } from "@/lib/presentation";
import type {
  AgentRun,
  AgentTopology,
  BackgroundTask,
  CustomerTicket,
  CustomerTicketCreateInput,
  DashboardData,
  HealthData,
  KnowledgeDocument,
  KnowledgeImportInput,
  KnowledgeSearchResult,
  PerformanceMetrics,
  TicketDetail,
  TicketSummary,
  Product, ProductInput, StoreOrder, QuestionRoute,
} from "@/lib/types";

let base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";
if (typeof window !== "undefined") {
  const parsed = new URL(base, window.location.origin);
  if (["localhost", "127.0.0.1"].includes(parsed.hostname) && ["localhost", "127.0.0.1"].includes(window.location.hostname)) {
    parsed.hostname = window.location.hostname;
    base = parsed.toString().replace(/\/$/, "");
  }
}
const API_URL = base;
let csrfToken = "";
export interface SessionUser { id: string; username: string; display_name: string; role: "customer" | "admin"; workspace_id: string; active: boolean }
interface LoginResult { user: SessionUser; csrf_token: string }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const mutating = Boolean(init?.method && !["GET", "HEAD", "OPTIONS"].includes(init.method));
  if (mutating && !csrfToken && !["/auth/login", "/auth/register"].includes(path)) {
    const session = await fetch(`${API_URL}/auth/me`, { credentials: "include", cache: "no-store" });
    if (session.ok) csrfToken = ((await session.json()) as LoginResult).csrf_token;
  }
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(mutating && csrfToken ? { "X-CSRF-Token": csrfToken } : {}), ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: "请求失败" }));
    if (response.status === 401 && typeof window !== "undefined" && !["/auth/login", "/auth/register"].includes(path)) window.dispatchEvent(new Event("resolveai-session-expired"));
    throw new Error(typeof payload.detail === "string" ? serviceDisplayText(payload.detail) : `请求校验失败（HTTP ${response.status}）`);
  }
  return response.json() as Promise<T>;
}

export function watchBackgroundTask(
  taskId: string,
  onProgress?: (task: BackgroundTask) => void,
): Promise<BackgroundTask> {
  return new Promise((resolve, reject) => {
    const source = new EventSource(`${API_URL}/tasks/${encodeURIComponent(taskId)}/events`, { withCredentials: true });
    let settled = false;

    const parse = (event: MessageEvent<string>) => JSON.parse(event.data) as BackgroundTask;
    source.addEventListener("progress", (event) => onProgress?.(parse(event as MessageEvent<string>)));
    source.addEventListener("completed", (event) => {
      const task = parse(event as MessageEvent<string>);
      settled = true;
      source.close();
      onProgress?.(task);
      resolve(task);
    });
    source.addEventListener("failed", (event) => {
      const task = parse(event as MessageEvent<string>);
      settled = true;
      source.close();
      onProgress?.(task);
      reject(new Error(task.error || task.message || "后台任务执行失败"));
    });
    source.onerror = () => {
      if (settled) return;
      settled = true;
      source.close();
      reject(new Error("实时进度连接中断，请检查 Redis 与 Worker 状态"));
    };
  });
}

export const api = {
  products: (customer = false, q = "") => request<Product[]>(`${customer ? "/customer" : ""}/products?q=${encodeURIComponent(q)}`),
  saveProduct: (payload: ProductInput, id?: string) => request<Product>(id ? `/products/${id}` : "/products", {method: id ? "PUT" : "POST", body: JSON.stringify(payload)}),
  orders: (customer = false) => request<StoreOrder[]>(`${customer ? "/customer" : ""}/orders`),
  purchase: (product_id: string, quantity: number, address: string, idempotency_key: string) => request<StoreOrder>("/customer/orders", {method: "POST", body: JSON.stringify({product_id, quantity, address, idempotency_key})}),
  orderAction: (id: string, action: string, customer = false, extra: {transit_days?: number; expected_delivery_days?: number} = {}) => request<StoreOrder>(`${customer ? "/customer" : ""}/orders/${id}/action`, {method: "POST", body: JSON.stringify({action, ...extra})}),
  questionRoutes: (q = "", offset = 0) => request<QuestionRoute[]>(`/questions/routes?q=${encodeURIComponent(q)}&offset=${offset}&limit=100`),
  addFaq: (payload: KnowledgeImportInput) => request<KnowledgeDocument>("/knowledge/documents", {method: "POST", body: JSON.stringify(payload)}),
  toggleKnowledge: (id: string, enabled: boolean) => request<KnowledgeDocument>(`/knowledge/documents/${id}`, {method: "PATCH", body: JSON.stringify({enabled})}),
  customerHistory: () => request<CustomerTicket[]>("/customer/tickets"),
  session: async () => { const result = await request<LoginResult>("/auth/me"); csrfToken = result.csrf_token; return result.user; },
  login: async (username: string, password: string) => { const result = await request<LoginResult>("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }); csrfToken = result.csrf_token; return result.user; },
  register: async (username: string, password: string, display_name: string, auto_login = true) => { const result = await request<LoginResult>("/auth/register", { method: "POST", body: JSON.stringify({ username, password, display_name, auto_login }) }); csrfToken = result.csrf_token; return result.user; },
  logout: async () => { await request("/auth/logout", { method: "POST" }); csrfToken = ""; },
  changePassword: async (current_password: string, new_password: string) => { const result = await request<LoginResult>("/auth/password", { method: "POST", body: JSON.stringify({ current_password, new_password }) }); csrfToken = result.csrf_token; return result.user; },
  users: () => request<SessionUser[]>("/auth/users"),
  updateUser: (id: string, payload: { display_name: string }) => request<SessionUser>(`/auth/users/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  health: () => request<HealthData>("/health"),
  dashboard: () => request<DashboardData>("/dashboard"),
  performanceMetrics: () => request<PerformanceMetrics>("/performance/metrics"),
  agentTopology: () => request<AgentTopology>("/agents/topology"),
  agentRuns: (limit = 30) => request<AgentRun[]>(`/agents/runs?limit=${limit}`),
  knowledgeDocuments: () => request<KnowledgeDocument[]>("/knowledge/documents"),
  knowledgeDocument: (id: string) => request<KnowledgeDocument>(`/knowledge/documents/${id}`),
  searchKnowledge: (query: string) => request<KnowledgeSearchResult>(`/knowledge/search?q=${encodeURIComponent(query)}`),
  tickets: (status = "all", caseType: "all" | "consultation" | "after_sales" = "all") => request<TicketSummary[]>(`/tickets?status=${status}&case_type=${caseType}`),
  ticket: (id: string) => request<TicketDetail>(`/tickets/${id}`),
  analyze: (id: string) => request<TicketDetail>(`/tickets/${id}/analyze`, { method: "POST" }),
  startAgentAnalysis: (id: string) => request<BackgroundTask>(`/tasks/agent-analysis/${id}`, { method: "POST" }),
  backgroundTask: (id: string) => request<BackgroundTask>(`/tasks/${id}`),
  importKnowledge: (documents: KnowledgeImportInput[]) =>
    request<BackgroundTask>("/tasks/knowledge-import", {
      method: "POST",
      body: JSON.stringify({ documents }),
    }),
  startEvaluation: (limit?: number) =>
    request<BackgroundTask>("/tasks/evaluation", {
      method: "POST",
      body: JSON.stringify({ limit: limit ?? null }),
    }),
  rewriteDraft: (id: string, instruction = "保持政策与金额不变，改写得更自然、简洁、有同理心") =>
    request<TicketDetail>(`/tickets/${id}/draft/rewrite`, {
      method: "POST",
      body: JSON.stringify({ instruction }),
    }),
  sendResponse: (id: string, response: string) =>
    request<TicketDetail>(`/tickets/${id}/draft/send`, {
      method: "POST",
      body: JSON.stringify({ response }),
    }),
  decide: (id: string, decision: "approve" | "reject", note = "") =>
    request<TicketDetail>(`/tickets/${id}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision, note, reviewer: "产品经理" }),
    }),
  executeApprovedRefund: (id: string, approvalId: string, orderId: string, amount: number) =>
    request<{ id: string; replayed: boolean; result: { mode: string; success: boolean } }>(`/tickets/${id}/tools/execute_refund/execute`, {
      method: "POST",
      body: JSON.stringify({
        idempotency_key: `ui-refund:${id}:${approvalId}`,
        arguments: { order_id: orderId, amount, currency: "CNY", reason: "人工批准后的项目模拟退款" },
      }),
    }),
  createCustomerTicket: (payload: CustomerTicketCreateInput) =>
    request<CustomerTicket>("/customer/tickets", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  customerTicket: (id: string) => request<CustomerTicket>(`/customer/tickets/${id}`),
  deleteCustomerChat: (id: string) => request<{ id: string; deleted: boolean }>(`/customer/tickets/${encodeURIComponent(id)}`, { method: "DELETE" }),
  sendCustomerMessage: (id: string, message: string) =>
    request<CustomerTicket>(`/customer/tickets/${id}/messages`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
  confirmCustomerTicket: (id: string, resolved: boolean, rating: number | null, feedback: string) =>
    request<CustomerTicket>(`/customer/tickets/${id}/confirmation`, {
      method: "POST",
      body: JSON.stringify({ resolved, rating, feedback }),
    }),
};
