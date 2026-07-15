/**
 * API-клиент Roobico.
 *
 * Аутентификация: cookie-сессия Flask (куки хранит нативный стек),
 * CSRF-токен приходит из /api/mobile/login|session и уходит в X-CSRFToken
 * на каждом POST. База API настраивается на экране логина.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";

const BASE_URL_KEY = "roobico.baseUrl";
export const DEFAULT_BASE_URL = "https://app.roobico.com";

let baseUrl: string = DEFAULT_BASE_URL;
let csrfToken: string = "";

export async function loadBaseUrl(): Promise<string> {
  try {
    const saved = await AsyncStorage.getItem(BASE_URL_KEY);
    if (saved) baseUrl = saved;
  } catch {
    // ignore storage failures
  }
  return baseUrl;
}

export async function setBaseUrl(url: string): Promise<void> {
  baseUrl = url.replace(/\/+$/, "");
  try {
    await AsyncStorage.setItem(BASE_URL_KEY, baseUrl);
  } catch {
    // ignore storage failures
  }
}

export function getBaseUrl(): string {
  return baseUrl;
}

export function setCsrfToken(token: string) {
  csrfToken = token || "";
}

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(message: string, status: number, code = "") {
    super(message);
    this.status = status;
    this.code = code;
  }
}

// Вызывается при 401 на любом запросе (кроме auth-эндпоинтов) —
// AuthProvider подписывается и сбрасывает сессию (экран логина).
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (options.method && options.method !== "GET") {
    // Для FormData Content-Type выставляет сам fetch (с boundary).
    if (!(options.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }
    if (csrfToken) headers["X-CSRFToken"] = csrfToken;
  }

  let res: Response;
  try {
    res = await fetch(`${baseUrl}${path}`, {
      ...options,
      headers,
      credentials: "include",
    });
  } catch (e) {
    throw new ApiError("Network error. Check the server address and connection.", 0, "network");
  }

  let data: any = null;
  try {
    data = await res.json();
  } catch {
    throw new ApiError(`Unexpected server response (${res.status}).`, res.status, "bad_response");
  }

  if (!res.ok || (data && data.ok === false)) {
    if (res.status === 401 && !path.startsWith("/api/mobile/login") && !path.startsWith("/api/mobile/session")) {
      onUnauthorized?.();
    }
    const message = (data && (data.message || data.error)) || `Request failed (${res.status}).`;
    throw new ApiError(String(message), res.status, String((data && data.error) || ""));
  }

  return data as T;
}

// ── Типы ответов ────────────────────────────────────────────────────

export interface SessionInfo {
  ok: boolean;
  csrf_token: string;
  user: { id: string; email: string; role: string };
  tenant: { id: string; name: string };
  shops: { id: string; name: string }[];
  active_shop_id: string;
  permissions: string[];
}

export interface Pagination {
  page: number;
  pages: number;
  total: number;
  per_page: number;
  has_prev: boolean;
  has_next: boolean;
}

export interface WorkOrderRow {
  id: string;
  wo_number: number | null;
  customer: string;
  date: string;
  unit: string;
  mileage: number | null;
  // Денежные поля отсутствуют в ответе для механиков (без view_costs).
  labor_total?: number;
  parts_total?: number;
  sales_tax_total?: number;
  grand_total?: number;
  paid_amount?: number;
  balance?: number;
  is_paid: boolean;
  is_in_progress: boolean;
  status: string;
}

export interface CustomerRow {
  id: string;
  company_name: string;
  contact_name: string;
  phone: string;
  email: string;
  address: string;
  is_active: boolean;
}

export interface VendorRow {
  id: string;
  name: string;
  contact_name: string;
  phone: string;
  email: string;
  website: string;
  address: string;
  is_active: boolean;
}

export interface PartRow {
  id: string;
  part_number: string;
  description: string;
  reference: string;
  in_stock: number;
  average_cost: number;
  selling_price: number;
}

export interface ListResponse<T> {
  ok: boolean;
  items: T[];
  pagination: Pagination;
  totals?: Record<string, number>;
}

export interface DashboardMetrics {
  ok: boolean;
  period_paid_amount: number;
  period_unpaid_amount: number;
  period_labor_total: number;
  period_parts_total: number;
  period_grand_total: number;
  period_money_total: number;
  period_total: number;
  paid_percent: number;
  outstanding_balance: number;
  period_parts_orders_total: number;
  period_parts_orders_received: number;
  period_parts_orders_paid_amount: number;
  period_parts_orders_unpaid_amount: number;
}

// ── Auth ────────────────────────────────────────────────────────────

export async function apiLogin(email: string, password: string): Promise<SessionInfo> {
  const data = await request<SessionInfo>("/api/mobile/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  setCsrfToken(data.csrf_token);
  return data;
}

export async function apiSession(): Promise<SessionInfo> {
  const data = await request<SessionInfo>("/api/mobile/session");
  setCsrfToken(data.csrf_token);
  return data;
}

export async function apiLogout(): Promise<void> {
  await request<{ ok: boolean }>("/api/mobile/logout", { method: "POST" });
  setCsrfToken("");
}

export async function apiSetActiveShop(shopId: string): Promise<void> {
  await request<{ ok: boolean }>("/api/mobile/active-shop", {
    method: "POST",
    body: JSON.stringify({ shop_id: shopId }),
  });
}

// ── Списки ──────────────────────────────────────────────────────────

function listQuery(q: string, page: number): string {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  params.set("page", String(page));
  return params.toString();
}

export function fetchWorkOrders(
  q: string,
  page: number,
  opts: { paidStatus?: string; customerId?: string; unitId?: string } = {}
) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  params.set("page", String(page));
  if (opts.paidStatus && opts.paidStatus !== "all") params.set("paid_status", opts.paidStatus);
  if (opts.customerId) params.set("customer_id", opts.customerId);
  if (opts.unitId) params.set("unit_id", opts.unitId);
  return request<ListResponse<WorkOrderRow>>(`/api/mobile/work_orders?${params.toString()}`);
}

export function fetchCustomers(q: string, page: number) {
  return request<ListResponse<CustomerRow>>(`/api/mobile/customers?${listQuery(q, page)}`);
}

export function fetchVendors(q: string, page: number) {
  return request<ListResponse<VendorRow>>(`/api/mobile/vendors?${listQuery(q, page)}`);
}

export function fetchParts(q: string, page: number) {
  return request<ListResponse<PartRow>>(`/api/mobile/parts?${listQuery(q, page)}`);
}

// ── Деталки ─────────────────────────────────────────────────────────

export interface WoPartDetail {
  part_number: string;
  description: string;
  label: string;
  qty: number;
  price: number;
  price_fmt: string;
  total: number;
  core: number;
  misc: number;
  misc_desc: string;
}

export interface WoLaborDetail {
  labor_desc: string;
  hours: string;
  rate_code: string;
  issue_description: string;
  labor_base: number;
  shop_supply: number;
  labor_total: number;
  parts_subtotal: number;
  core_total: number;
  misc_total: number;
  block_total: number;
  parts: WoPartDetail[];
  misc_items: { description: string; qty: number; price: number; total: number }[];
}

export interface WorkOrderDetails {
  ok: boolean;
  id: string;
  status: string;
  is_estimate: boolean;
  customer_id: string;
  unit_id: string;
  wo_number: string;
  wo_date_label: string;
  cust_name: string;
  customer_email: string;
  customer_phone: string;
  customer_address: string;
  unit_label: string;
  unit_vin: string;
  unit_mileage: string;
  labors: WoLaborDetail[];
  t: {
    labor_total: number;
    parts_total: number;
    core_total: number;
    shop_supply_total: number;
    misc_total: number;
    sales_tax_total: number;
    grand_total: number;
    paid_total: number;
    remaining_balance: number;
    is_fully_paid: boolean;
  };
}

export function fetchWorkOrderDetails(id: string) {
  return request<WorkOrderDetails>(`/api/mobile/work_orders/${encodeURIComponent(id)}`);
}

// ── Механик-режим: безденежные детали WO + таймеры работ ────────────

export interface MechRunningUser {
  user_id: string;
  user_name: string;
  started_at: string; // ISO UTC
  mine: boolean;
}

export interface MechLaborTime {
  total_seconds: number; // завершённые + идущие (на момент ответа)
  completed_seconds: number; // только завершённые сессии — база для тикеров
  my_seconds: number;
  my_running: boolean;
  my_started_at: string;
  // Над одной работой могут работать несколько механиков одновременно.
  running_users: MechRunningUser[];
}

export interface MechWoPart {
  part_id: string;
  part_number: string;
  description: string;
  qty: number;
  one_time_part: boolean;
}

export interface MechWoLabor {
  labor_id: string;
  description: string;
  issue_description: string;
  parts: MechWoPart[];
  time: MechLaborTime;
}

export interface MechRunningTimer {
  work_order_id: string;
  wo_number: number | string | null;
  labor_id: string;
  started_at: string;
  labor_description?: string;
}

export interface MechWorkOrderDetails {
  ok: boolean;
  id: string;
  wo_number: number | string | null;
  status: string;
  customer: { id: string; label: string };
  unit: { id: string; label: string; vin: string; unit_number: string; mileage: number | string | null };
  date: string;
  labors: MechWoLabor[];
  my_running_timer: MechRunningTimer | null;
  server_now: string;
}

/** Для механиков /api/mobile/work_orders/<id> возвращает эту форму (без цен). */
export function fetchMechanicWoDetails(id: string) {
  return request<MechWorkOrderDetails>(`/api/mobile/work_orders/${encodeURIComponent(id)}`);
}

export interface TimerLog {
  id: string;
  work_order_id: string;
  wo_number: number | string | null;
  labor_id: string;
  user_id: string;
  user_name: string;
  started_at: string;
  stopped_at: string;
  seconds: number | null;
}

export interface TimerResponse {
  ok: boolean;
  timer: TimerLog;
  stopped_previous?: TimerLog | null;
  time_summary: Record<string, MechLaborTime>;
  server_now: string;
}

export function startLaborTimer(woId: string, laborId: string) {
  return request<TimerResponse>("/work_orders/api/mechanic/timers/start", {
    method: "POST",
    body: JSON.stringify({ work_order_id: woId, labor_id: laborId }),
  });
}

export function stopLaborTimer() {
  return request<TimerResponse>("/work_orders/api/mechanic/timers/stop", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function fetchCurrentTimer() {
  return request<{ ok: boolean; timer: (MechRunningTimer & { labor_description?: string }) | null; server_now: string }>(
    "/work_orders/api/mechanic/timers/current"
  );
}

export interface ActiveTimerItem {
  work_order_id: string;
  wo_number: number | string | null;
  customer: string;
  labor_id: string;
  labor_description: string;
  user_id: string;
  user_name: string;
  started_at: string;
  mine: boolean;
}

/** Все идущие таймеры магазина — блок «сейчас в работе» над списком WO. */
export function fetchActiveTimers() {
  return request<{ ok: boolean; items: ActiveTimerItem[]; server_now: string }>(
    "/work_orders/api/mechanic/timers/active"
  );
}

export interface PaymentRow {
  id: string;
  amount: number;
  payment_method: string;
  notes: string;
  payment_date: string | null;
  payment_date_label: string;
}

export interface WoPayments {
  ok: boolean;
  payments: PaymentRow[];
  grand_total: number;
  paid_amount: number;
  remaining_balance: number;
  status?: string;
}

export function fetchWoPayments(woId: string) {
  return request<WoPayments>(`/work_orders/api/work_orders/${encodeURIComponent(woId)}/payments`);
}

export function recordWoPayment(woId: string, amount: number, method: string, notes: string) {
  return request<{ ok: boolean; status: string; remaining_balance: number }>(
    `/work_orders/api/work_orders/${encodeURIComponent(woId)}/payment`,
    { method: "POST", body: JSON.stringify({ amount, payment_method: method, notes }) }
  );
}

export function deleteWoPayment(paymentId: string) {
  return request<{ ok: boolean; status: string }>(
    `/work_orders/api/payments/${encodeURIComponent(paymentId)}/delete`,
    { method: "POST", body: JSON.stringify({}) }
  );
}

export interface CustomerDetails {
  ok: boolean;
  id: string;
  label: string;
  company_name: string;
  address: string;
  taxable: boolean;
  is_active: boolean;
  contacts: {
    name: string;
    first_name: string;
    last_name: string;
    phone: string;
    email: string;
    is_main: boolean;
  }[];
  units: {
    id: string;
    label: string;
    unit_number: string;
    vin: string;
    year: string;
    make: string;
    model: string;
    mileage: number | null;
  }[];
}

export function fetchCustomerDetails(id: string) {
  return request<CustomerDetails>(`/api/mobile/customers/${encodeURIComponent(id)}`);
}

export interface UnitDetails {
  ok: boolean;
  id: string;
  label: string;
  customer_id: string;
  customer_label: string;
  unit_number: string;
  vin: string;
  year: string;
  make: string;
  model: string;
  type: string;
  mileage: number | null;
  is_active: boolean;
  annual_inspection: { date: string; inspector: string } | null;
}

export function fetchUnitDetails(id: string) {
  return request<UnitDetails>(`/api/mobile/units/${encodeURIComponent(id)}`);
}

export function fetchCustomerBalance(customerId: string): Promise<number> {
  const params = new URLSearchParams();
  params.append("ids", customerId);
  return request<{ ok: boolean; balances: Record<string, number> }>(
    `/customers/api/balances?${params.toString()}`
  ).then((d) => Number(d.balances?.[customerId] ?? 0));
}

// ── Payments / Estimates (вкладки Work Orders) ──────────────────────

export interface AllPaymentRow {
  id: string;
  work_order_id: string;
  wo_number: number | string;
  customer: string;
  amount: number;
  payment_method: string;
  notes: string;
  payment_date: string;
}

export function fetchAllPayments(q: string, page: number) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  params.set("payments_page", String(page));
  params.set("date_preset", "all_time");
  return request<{ ok: boolean; payments: AllPaymentRow[]; pagination: Pagination }>(
    `/work_orders/api/work_orders/all-payments?${params.toString()}`
  ).then((d) => ({ ok: d.ok, items: d.payments, pagination: d.pagination } as ListResponse<AllPaymentRow>));
}

export function fetchEstimates(q: string, page: number) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  params.set("page", String(page));
  return request<{ ok: boolean; estimates: WorkOrderRow[]; pagination: Pagination }>(
    `/work_orders/api/estimates?${params.toString()}`
  ).then((d) => ({ ok: d.ok, items: d.estimates, pagination: d.pagination } as ListResponse<WorkOrderRow>));
}

// ── Part / Vendor детали ────────────────────────────────────────────

export interface PartFull {
  _id: string;
  part_number: string;
  description: string;
  reference: string;
  vendor_id: string;
  category_id: string;
  location_id: string;
  in_stock: number;
  do_not_track_inventory: boolean;
  average_cost: number;
  has_selling_price: boolean;
  selling_price: number;
  core_has_charge: boolean;
  core_cost: number;
}

export function fetchPartFull(id: string) {
  return request<{ ok: boolean; item: PartFull }>(`/parts/api/${encodeURIComponent(id)}`).then(
    (d) => d.item
  );
}

export interface PartHistoryOrder {
  order_id: string;
  order_number: string | number;
  status: string;
  vendor: string;
  quantity: number;
  price: number;
  created_at: string | null;
  received_at: string | null;
}

export function fetchPartHistory(id: string) {
  return request<{ ok: boolean; orders: PartHistoryOrder[] }>(
    `/parts/api/${encodeURIComponent(id)}/history?orders_per_page=25`
  );
}

export interface VendorFull {
  _id: string;
  name: string;
  website: string;
  address: string;
  notes: string;
  is_active: boolean;
  contacts: { first_name?: string; last_name?: string; phone?: string; email?: string; is_main?: boolean }[];
}

export function fetchVendorFull(id: string) {
  return request<{ ok: boolean; item: VendorFull }>(`/vendors/api/${encodeURIComponent(id)}`).then(
    (d) => d.item
  );
}

export interface VendorOrderRow {
  id: string;
  order_number: string | number;
  items_count: number;
  total_amount: number;
  status: string;
  created_at: string;
}

export interface VendorOrdersSummary {
  total_orders: number;
  total_amount: number;
  total_paid: number;
  unpaid: number;
  received: number;
  not_received: number;
}

export function fetchVendorOrders(id: string, page: number) {
  const params = new URLSearchParams();
  params.set("page", String(page));
  params.set("per_page", "10");
  params.set("date_preset", "all_time");
  return request<{
    ok: boolean;
    items: VendorOrderRow[];
    pagination: Pagination & { prev_page?: number; next_page?: number };
    summary: VendorOrdersSummary | null;
  }>(`/vendors/api/${encodeURIComponent(id)}/part-orders?${params.toString()}`);
}

// ── CRUD ────────────────────────────────────────────────────────────

export interface ContactPayload {
  first_name?: string;
  last_name?: string;
  phone?: string;
  email?: string;
  is_main?: boolean;
}

export interface CustomerPayload {
  company_name: string;
  address: string;
  taxable: boolean;
  contacts: ContactPayload[];
}

export function createCustomer(payload: CustomerPayload) {
  return request<{ ok: boolean; id: string; label: string }>("/api/mobile/customers", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateCustomer(id: string, payload: CustomerPayload) {
  return request<{ ok: boolean }>(`/customers/api/${encodeURIComponent(id)}/update`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export interface UnitPayload {
  customer_id?: string;
  unit_number: string;
  vin: string;
  year: string;
  make: string;
  model: string;
  type: string;
  mileage: string;
}

export function createUnit(payload: UnitPayload) {
  return request<{ ok: boolean; id: string; label: string }>("/api/mobile/units", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateUnit(id: string, payload: UnitPayload) {
  return request<{ ok: boolean }>(`/api/mobile/units/${encodeURIComponent(id)}/update`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export interface VendorPayload {
  name: string;
  website: string;
  address: string;
  notes: string;
  contacts: ContactPayload[];
  is_active?: boolean;
}

export function createVendor(payload: VendorPayload) {
  return request<{ ok: boolean; id: string; name: string }>("/api/mobile/vendors", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateVendor(id: string, payload: VendorPayload) {
  return request<{ ok: boolean }>(`/vendors/api/${encodeURIComponent(id)}/update`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export interface VinDecoded {
  ok: boolean;
  vin: string;
  make: string;
  model: string;
  year: string;
  type: string;
  warning: string | null;
  suggested_vin: string | null;
}

export function decodeVin(vin: string) {
  return request<VinDecoded>(`/work_orders/api/vin?vin=${encodeURIComponent(vin)}`);
}

// ── WO editor ───────────────────────────────────────────────────────

export interface LaborRate {
  code: string;
  name: string;
  hourly_rate?: number; // сервер не отдаёт $/час механикам
}

export function fetchLaborRates() {
  return request<{ ok: boolean; items: LaborRate[] }>("/api/mobile/labor_rates").then(
    (d) => d.items
  );
}

export interface PartSearchItem {
  id: string;
  part_number: string;
  description: string;
  reference: string;
  average_cost?: number; // отсутствует для механиков
  in_stock: number;
}

export function searchParts(q: string) {
  return request<{ items: PartSearchItem[] }>(
    `/work_orders/api/parts/search?q=${encodeURIComponent(q)}`
  ).then((d) => d.items || []);
}

export function fetchPartPrice(partId: string, customerId: string) {
  const params = new URLSearchParams({ part_id: partId });
  if (customerId) params.set("customer_id", customerId);
  return request<{ ok: boolean; price: number; cost: number; core_charge: number }>(
    `/api/mobile/part_price?${params.toString()}`
  );
}

export interface WoFormPart {
  part_id: string;
  part_number: string;
  description: string;
  qty: number;
  cost?: number; // механики не шлют цен — сервер заполняет сам
  price?: number;
  core_charge?: number;
  misc_charge?: number;
  misc_charge_description?: string;
  one_time_part?: boolean;
}

export interface WoFormLabor {
  labor_id?: string; // стабильный ID строки: сервер сохраняет по нему часы/цены/таймеры
  description: string;
  hours: string;
  rate_code: string;
  labor_total?: number | null;
  issue_description?: string;
  parts: WoFormPart[];
}

export interface WoSaveResult {
  ok: boolean;
  id: string;
  wo_number?: number;
  status: string;
  grand_total?: number; // отсутствует в ответах механик-эндпоинтов
  inventory_warnings: string[];
}

export function createWorkOrder(payload: {
  customer_id: string;
  unit_id: string;
  status: string;
  labors: WoFormLabor[];
}) {
  return request<WoSaveResult>("/api/mobile/work_orders", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function editWorkOrder(id: string, payload: { status?: string; labors: WoFormLabor[] }) {
  return request<WoSaveResult>(`/api/mobile/work_orders/${encodeURIComponent(id)}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export interface PresetListItem {
  id: string;
  name: string;
}

export function fetchPresets() {
  return request<PresetListItem[]>("/work_orders/api/presets");
}

export interface PresetDetail {
  id: string;
  name: string;
  description: string;
  labor_hours: number | string | null;
  labor_rate_code: string | null;
  parts: {
    part_id: string;
    part_number: string;
    description: string;
    qty: number;
    cost?: number; // отсутствует для механиков (сервер стрипит деньги)
    price?: number | null;
    core_has_charge?: boolean;
    core_cost?: number;
  }[];
}

export function fetchPresetDetail(id: string) {
  return request<PresetDetail>(`/work_orders/api/presets/${encodeURIComponent(id)}`);
}

export function polishIssueText(text: string) {
  return request<{ ok: boolean; polished: string; original: string }>(
    "/work_orders/api/ai/polish-issue",
    { method: "POST", body: JSON.stringify({ text }) }
  );
}

export function sendWoAuthorization(woId: string, email: string) {
  return request<{ ok: boolean }>(
    `/work_orders/api/work_orders/${encodeURIComponent(woId)}/send-authorization`,
    { method: "POST", body: JSON.stringify({ email, scope: "work_order" }) }
  );
}

export function deleteWorkOrder(id: string) {
  return request<{ ok: boolean }>(
    `/work_orders/api/work_orders/${encodeURIComponent(id)}/delete`,
    { method: "POST", body: JSON.stringify({}) }
  );
}

export function setWorkOrderStatus(id: string, status: "open" | "in_progress") {
  return request<{ ok: boolean; status: string }>(
    `/work_orders/api/work_orders/${encodeURIComponent(id)}/status`,
    { method: "POST", body: JSON.stringify({ status }) }
  );
}

// ── Parts orders ────────────────────────────────────────────────────

export interface PartsOrderRow {
  id: string;
  order_number: number | string | null;
  vendor: string;
  vendor_bill: string;
  items_count: number;
  status: string;
  total_amount: number;
  paid_amount: number;
  remaining_balance: number;
  payment_status: string;
  created_at: string;
}

export function fetchPartsOrders(q: string, page: number) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  params.set("page", String(page));
  return request<ListResponse<PartsOrderRow>>(`/api/mobile/parts_orders?${params.toString()}`);
}

export interface PartsOrderDetail {
  id: string;
  vendor_id: string;
  status: string;
  vendor_bill: string;
  order_date: string;
  created_at: string | null;
  received_at: string | null;
  items: {
    part_id: string;
    part_number: string;
    description: string;
    quantity: number;
    price: number;
    core_charge: number;
  }[];
  non_inventory_amounts: { type: string; description: string; amount: number }[];
  payment_summary: {
    total_amount: number;
    paid_amount: number;
    remaining_balance: number;
    payment_status: string;
  };
}

export function fetchPartsOrder(id: string) {
  return request<{ ok: boolean; order: PartsOrderDetail }>(
    `/parts/api/orders/${encodeURIComponent(id)}`
  ).then((d) => d.order);
}

export function createPartsOrder(payload: {
  vendor_id: string;
  items: { part_id: string; quantity: number; price: number }[];
}) {
  return request<{ ok: boolean; order_id: string }>("/parts/api/orders/create", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function receivePartsOrder(id: string, vendorBill: string) {
  return request<{ ok: boolean }>(`/parts/api/orders/${encodeURIComponent(id)}/receive`, {
    method: "POST",
    body: JSON.stringify({ vendor_bill: vendorBill }),
  });
}

export function unreceivePartsOrder(id: string) {
  return request<{ ok: boolean }>(`/parts/api/orders/${encodeURIComponent(id)}/unreceive`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function payPartsOrder(id: string, amount: number, method: string, notes: string) {
  return request<{ ok: boolean }>(`/parts/api/orders/${encodeURIComponent(id)}/payment`, {
    method: "POST",
    body: JSON.stringify({ amount, payment_method: method, notes }),
  });
}

// ── Calendar ────────────────────────────────────────────────────────

export interface CalendarEvent {
  id: string;
  title: string;
  start_time: string;
  end_time: string;
  status: string;
  customer_id: string;
  customer_label: string;
  unit_id: string;
  unit_label: string;
  mechanic_id: string;
  mechanic_name: string;
  presets: { id: string; name: string }[];
}

export function fetchCalendarEvents(startIso: string, endIso: string) {
  const params = new URLSearchParams({ start: startIso, end: endIso });
  return request<{ events?: CalendarEvent[]; items?: CalendarEvent[] } | CalendarEvent[]>(
    `/calendar/api/events?${params.toString()}`
  ).then((d: any) => (Array.isArray(d) ? d : d.events || d.items || []));
}

export function createCalendarEvent(payload: {
  start_time: string;
  end_time: string;
  customer_id?: string;
  customer_label?: string;
  unit_id?: string;
  unit_label?: string;
  mechanic_id?: string;
  mechanic_name?: string;
  status?: string;
  title?: string;
}) {
  return request<{ ok?: boolean; id?: string }>("/calendar/api/events", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export interface CalendarStatus {
  key: string;
  label: string;
  color: string;
}

export function fetchCalendarStatuses() {
  return request<CalendarStatus[]>("/calendar/api/statuses");
}

export function updateCalendarEvent(
  id: string,
  payload: Partial<{
    start_time: string;
    end_time: string;
    customer_id: string;
    customer_label: string;
    unit_id: string;
    unit_label: string;
    mechanic_id: string;
    mechanic_name: string;
    status: string;
    title: string;
  }>
) {
  return request<{ ok?: boolean }>(`/calendar/api/events/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function deleteCalendarEvent(id: string) {
  return request<{ ok: boolean }>(`/calendar/api/events/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export function fetchCalendarMechanics() {
  return request<any>("/calendar/api/mechanics").then(
    (d: any) => (Array.isArray(d) ? d : d.items || d.mechanics || []) as { id: string; name: string }[]
  );
}

// ── Attachments ─────────────────────────────────────────────────────

export interface AttachmentItem {
  id: string;
  filename: string;
  content_type: string;
  size: number;
  is_image?: boolean;
}

export function fetchAttachments(entityType: string, entityId: string) {
  const params = new URLSearchParams({ entity_type: entityType, entity_id: entityId });
  return request<{ ok: boolean; items: AttachmentItem[] }>(
    `/attachments/api/list?${params.toString()}`
  ).then((d) =>
    (d.items || []).map((x: any) => ({
      id: String(x.id || x._id || ""),
      filename: String(x.filename || "file"),
      content_type: String(x.content_type || ""),
      size: Number(x.size || 0),
      is_image: String(x.content_type || "").startsWith("image/"),
    }))
  );
}

export function attachmentDownloadUrl(id: string): string {
  return `${baseUrl}/attachments/api/${encodeURIComponent(id)}/download`;
}

export function uploadAttachment(
  entityType: string,
  entityId: string,
  file: { uri: string; name: string; type: string }
) {
  const form = new FormData();
  form.append("entity_type", entityType);
  form.append("entity_id", entityId);
  form.append("files", file as unknown as Blob);
  return request<{ ok: boolean }>("/attachments/api/upload", {
    method: "POST",
    body: form,
  });
}

export function deleteAttachment(id: string) {
  return request<{ ok: boolean }>(`/attachments/api/${encodeURIComponent(id)}/delete`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

// ── AI-сканы ────────────────────────────────────────────────────────

export interface InvoiceScanResult {
  ok: boolean;
  vendor_name: string;
  vendor_match: { vendor_id: string; vendor_name: string } | null;
  invoice_number: string;
  total: number;
  items: {
    part_number?: string;
    description?: string;
    quantity?: number;
    qty?: number;
    price?: number;
    unit_price?: number;
    matched_part: {
      part_id: string;
      part_number: string;
      description: string;
      average_cost: number;
    } | null;
  }[];
}

export function parseInvoiceScan(file: { uri: string; name: string; type: string }) {
  const form = new FormData();
  form.append("invoice", file as unknown as Blob);
  return request<InvoiceScanResult>("/parts/api/orders/parse-invoice", {
    method: "POST",
    body: form,
  });
}

export interface HandwrittenScanResult {
  ok: boolean;
  labors: {
    labor_description: string;
    labor_hours: number | null;
    parts: {
      written_part_number: string;
      written_description: string;
      qty: number;
      candidates: {
        part_id: string;
        part_number: string;
        description: string;
        average_cost: number;
        selling_price: number;
      }[];
    }[];
  }[];
}

export function parseHandwrittenWo(file: { uri: string; name: string; type: string }) {
  const form = new FormData();
  form.append("file", file as unknown as Blob);
  return request<HandwrittenScanResult>("/work_orders/api/parse-handwritten", {
    method: "POST",
    body: form,
  });
}

// ── Global search ───────────────────────────────────────────────────

export interface GlobalSearchGroup {
  category: string;
  items: { label: string; url: string; inactive?: boolean }[];
}

export function globalSearch(q: string) {
  return request<{ results: GlobalSearchGroup[] }>(
    `/api/global-search?q=${encodeURIComponent(q)}`
  ).then((d) => d.results || []);
}

// ── Reports ─────────────────────────────────────────────────────────

export interface StandardReport {
  ok: boolean;
  selected_tab: string;
  shop_name: string;
  report_data: {
    title: string;
    summary: Record<string, unknown>;
    rows: Record<string, unknown>[];
    chart_data: unknown;
  };
}

export function fetchStandardReport(key: string, datePreset: string) {
  const params = new URLSearchParams();
  if (datePreset) params.set("date_preset", datePreset);
  return request<StandardReport>(
    `/reports/api/standard/${encodeURIComponent(key)}?${params.toString()}`
  );
}

export function sendWorkOrderEmail(woId: string, email: string) {
  return request<{ ok: boolean }>(
    `/work_orders/api/work_orders/${encodeURIComponent(woId)}/send-email`,
    { method: "POST", body: JSON.stringify({ email }) }
  );
}

// ── Dashboard ───────────────────────────────────────────────────────

export function fetchDashboardMetrics(datePreset = "this_month") {
  const params = new URLSearchParams();
  params.set("date_preset", datePreset);
  return request<DashboardMetrics>(`/dashboard/api/metrics?${params.toString()}`);
}

export function money(value: number | null | undefined): string {
  const num = Number(value ?? 0);
  return `$${num.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",")}`;
}
