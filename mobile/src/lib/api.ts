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
  // Тело ответа сервера: обработчики читают из него детали (например,
  // work_order при 409 unit_has_active_work_order).
  data: any;

  constructor(message: string, status: number, code = "", data: any = null) {
    super(message);
    this.status = status;
    this.code = code;
    this.data = data;
  }
}

// Вызывается при 401 на любом запросе (кроме auth-эндпоинтов) —
// AuthProvider подписывается и сбрасывает сессию (экран логина).
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler;
}

// Таймауты запросов формы WO. Сеть «подключена, но не отвечает» (Wi-Fi
// цеха без интернета) держит fetch без ответа как угодно долго (Android —
// бесконечно, iOS — до ~60 с), а сохранения механика идут строго по одному:
// один зависший запрос держал бы автосейв, Done, сохранение при уходе и
// опрос. Обрыв по таймауту — обычная ошибка: форма повторит сохранение
// (новые строки несут client_key, base не меняется — дублей не будет).
// Создание WO таймаут не получает: его повтор создал бы второй WO.
export const SAVE_TIMEOUT_MS = 30000;
export const REFRESH_TIMEOUT_MS = 20000;
export const REV_TIMEOUT_MS = 15000;

async function request<T>(path: string, options: RequestInit = {}, timeoutMs = 0): Promise<T> {
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

  // timeoutMs > 0 — запрос обрывается, если сервер не ответил за это время.
  let timedOut = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const controller = timeoutMs > 0 && typeof AbortController !== "undefined" ? new AbortController() : null;
  if (controller) {
    timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
  }
  const timeoutError = () =>
    new ApiError("The server did not answer in time. Check the connection and try again.", 0, "timeout");

  try {
    let res: Response;
    try {
      res = await fetch(`${baseUrl}${path}`, {
        ...options,
        headers,
        credentials: "include",
        ...(controller ? { signal: controller.signal } : {}),
      });
    } catch (e) {
      if (timedOut) throw timeoutError();
      throw new ApiError("Network error. Check the server address and connection.", 0, "network");
    }

    let data: any = null;
    try {
      data = await res.json();
    } catch {
      if (timedOut) throw timeoutError();
      throw new ApiError(`Unexpected server response (${res.status}).`, res.status, "bad_response");
    }

    if (!res.ok || (data && data.ok === false)) {
      if (res.status === 401 && !path.startsWith("/api/mobile/login") && !path.startsWith("/api/mobile/session")) {
        onUnauthorized?.();
      }
      const message = (data && (data.message || data.error)) || `Request failed (${res.status}).`;
      throw new ApiError(String(message), res.status, String((data && data.error) || ""), data);
    }

    return data as T;
  } finally {
    if (timer) clearTimeout(timer);
  }
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
  working_now?: string[];
  // Механики WO — из assigned_mechanics всех строк (кто фактически делает).
  mechanics?: string[];
  // Все работавшие механики поставили Done (для механика — его собственный Done).
  mechanic_done?: boolean;
  // Кто уже поставил Done, пока остальные ещё работают (частичный Done).
  mechanics_done?: string[];
  // Менеджер подтвердил работу механика: WO для механика закрыт.
  manager_confirmed?: boolean;
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

export interface PartCrossRef {
  id: string;
  part_number: string;
  description: string;
  in_stock: number;
}

/** Фильтр списка запчастей по минимуму: below — ниже минимума, near — ниже или близко к нему. */
export type PartStockLevel = "below" | "near";
/** Состояние запаса относительно минимума; null — минимум не задан или запаса хватает. */
export type PartStockState = PartStockLevel | null;

/** Количество с единицей измерения: «12 gal», «3 qt»; для штук (each) — просто число. */
export function stockQty(qty: number | null | undefined, unitShort?: string | null): string {
  const n = Number(qty ?? 0);
  return unitShort && unitShort !== "ea" ? `${n} ${unitShort}` : String(n);
}

/** Остаток в карточках: «×12» для штук (как раньше), «12 gal» для прочих единиц. */
export function stockLabel(qty: number | null | undefined, unitShort?: string | null): string {
  return unitShort && unitShort !== "ea" ? stockQty(qty, unitShort) : `×${Number(qty ?? 0)}`;
}

export interface PartRow {
  id: string;
  part_number: string;
  description: string;
  reference: string;
  in_stock: number;
  average_cost: number;
  selling_price: number;
  cross_refs?: PartCrossRef[];
  /** Единица измерения и минимальный остаток; у старых серверов полей нет (= each, минимум не задан). */
  unit?: string;
  unit_short?: string;
  unit_label?: string;
  min_stock_enabled?: boolean;
  min_stock?: number;
  stock_state?: PartStockState;
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

export async function apiLogout(pushToken?: string | null): Promise<void> {
  await request<{ ok: boolean }>("/api/mobile/logout", {
    method: "POST",
    body: JSON.stringify(pushToken ? { push_token: pushToken } : {}),
  });
  setCsrfToken("");
}

export async function apiRegisterPushToken(token: string, platform: string): Promise<void> {
  await request<{ ok: boolean }>("/api/mobile/push-token", {
    method: "POST",
    body: JSON.stringify({ token, platform }),
  });
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

/**
 * Клиенты для формы WO. В отличие от /api/mobile/customers (нужно право
 * customers.view, которого нет у механика) этот эндпоинт доступен по
 * work_orders.view — как на веб-странице механика. Поиска на сервере нет,
 * фильтруем на клиенте.
 */
export function fetchWoFormCustomers(q: string) {
  return request<{ ok: boolean; customers: any[] }>("/work_orders/api/customers").then((d) => {
    const rows: CustomerRow[] = (d.customers || []).map((c: any) => ({
      id: String(c.id || ""),
      company_name: String(c.company_name || c.label || ""),
      contact_name: String(c.contact_name || ""),
      phone: String(c.phone || ""),
      email: String(c.email || ""),
      address: String(c.address || ""),
      is_active: true,
    }));
    const needle = q.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((c) =>
      `${c.company_name} ${c.contact_name}`.toLowerCase().includes(needle)
    );
  });
}

/** Юниты клиента для формы WO — доступно механику (work_orders.create). */
// WO, над которым уже идёт работа по юниту (in_progress, не подтверждён
// менеджером) — второй механик открывает его вместо создания нового.
export interface UnitActiveWorkOrder {
  id: string;
  wo_number: number | string | null;
  status: string;
  mechanic_done: boolean;
  customer_id: string;
  working_now: string[];
}

export function fetchUnitActiveWorkOrder(unitId: string) {
  return request<{ ok: boolean; work_order: UnitActiveWorkOrder | null; message: string }>(
    `/work_orders/api/mechanic/units/${encodeURIComponent(unitId)}/active_work_order`
  );
}

export function fetchWoFormUnits(customerId: string) {
  return request<{ items: { id: string; label: string; mileage?: number | string | null }[] }>(
    `/work_orders/api/units?customer_id=${encodeURIComponent(customerId)}`
  ).then((d) => d.items || []);
}

export function fetchVendors(q: string, page: number) {
  return request<ListResponse<VendorRow>>(`/api/mobile/vendors?${listQuery(q, page)}`);
}

export interface PartsListResponse extends ListResponse<PartRow> {
  /**
   * Сколько активных партов магазина ниже минимума / ниже или близко к нему
   * (near включает below — как фильтр stock_level). Без учёта поиска.
   */
  counts?: { below: number; near: number };
}

export function fetchParts(q: string, page: number, opts: { stockLevel?: PartStockLevel | "" } = {}) {
  const params = new URLSearchParams(listQuery(q, page));
  // С фильтром по минимуму сервер показывает и нулевые остатки.
  if (opts.stockLevel) params.set("stock_level", opts.stockLevel);
  return request<PartsListResponse>(`/api/mobile/parts?${params.toString()}`);
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
  tracked_seconds?: number;
  tracked_label?: string;
  parts: WoPartDetail[];
  misc_items: { description: string; qty: number; price: number; total: number }[];
}

/**
 * Запись календаря, привязанная к WO (деталка WO, поле `appointments`).
 * Время — настенные часы магазина без зоны, как у CalendarEvent; note —
 * заметка записи (title, если это не просто имя клиента).
 */
export interface WoAppointment {
  id: string;
  start_time: string;
  end_time: string;
  status: string;
  title: string;
  note: string;
  mechanics: { id: string; name: string }[];
  presets: { id: string; name: string }[];
}

/** Строка WO для мобильного редактора менеджера (деталка WO, поле raw_labors). */
export interface WoRawLabor {
  labor_id: string;
  description: string;
  hours: string;
  rate_code: string;
  issue_description: string;
  /** Сохранённая база лейбора (ручные суммы веба не пересчитываются). */
  labor_base: number;
  parts: {
    part_id?: string | null;
    part_number?: string;
    description?: string;
    qty?: number;
    cost?: number;
    price?: number;
    core_charge?: number;
    misc_charge?: number;
    misc_charge_description?: string;
    one_time_part?: boolean;
  }[];
}

export interface WorkOrderDetails {
  ok: boolean;
  id: string;
  status: string;
  is_estimate: boolean;
  manager_confirmed?: boolean;
  /** Строки для формы правки; у старых серверов может не быть. */
  raw_labors?: WoRawLabor[];
  /** Ревизия строк WO (labors_rev) и edit view этого открытия — у старых серверов нет. */
  labors_rev?: number;
  view_id?: string;
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
  /** Привязанные записи календаря; у старых серверов поля нет. */
  appointments?: WoAppointment[];
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

/** Параметры открытия WO формой правки. */
export interface WoDetailsOptions {
  /** Форма шлёт view_id сама (протокол слияния, ?mp=1). */
  mergeProtocol?: boolean;
  /** Оборвать запрос, если сервер не ответил за столько мс (0/нет — без таймаута). */
  timeoutMs?: number;
}

function woDetailsPath(id: string, opts?: WoDetailsOptions) {
  return `/api/mobile/work_orders/${encodeURIComponent(id)}${opts?.mergeProtocol ? "?mp=1" : ""}`;
}

export function fetchWorkOrderDetails(id: string, opts?: WoDetailsOptions) {
  return request<WorkOrderDetails>(woDetailsPath(id, opts), {}, opts?.timeoutMs || 0);
}

/**
 * Дешёвый опрос «WO изменился?»: ревизия строк (labors_rev). Механику
 * статус не отдаётся — только locked (оплачен / смета / подтверждён) и его
 * Done; менеджеру — status и manager_confirmed. Старый сервер эндпоинта не
 * знает: 404 с кодом not_found (а не work_order_not_found).
 */
export interface WoRevInfo {
  ok: boolean;
  labors_rev: number;
  locked?: boolean;
  mechanic_done?: boolean;
  status?: string;
  manager_confirmed?: boolean;
}

export function fetchWorkOrderRev(id: string) {
  // С таймаутом: зависший опрос не должен навсегда выключить следующие.
  return request<WoRevInfo>(`/api/mobile/work_orders/${encodeURIComponent(id)}/rev`, {}, REV_TIMEOUT_MS);
}

/**
 * Ключ новой строки работы (client_key): ставится при создании строки и
 * живёт на ней до получения labor_id. Повтор сохранения после потерянного
 * ответа сервер узнаёт по нему и не создаёт вторую такую же работу; id
 * новых строк приходят в ответе как client_keys {client_key: labor_id}.
 */
export function newClientKey(): string {
  const rnd = () => Math.random().toString(36).slice(2, 10);
  return `ck-${Date.now().toString(36)}-${rnd()}${rnd()}`;
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
  manager_confirmed?: boolean;
  customer: { id: string; label: string };
  customer_email?: string;
  mechanic_done?: boolean;
  unit: { id: string; label: string; vin: string; unit_number: string; mileage: number | string | null };
  /** Пробег, записанный на самом WO (снимок на момент работы). */
  wo_mileage?: number | string | null;
  /** Механик обязан подтвердить текущий пробег до работы с WO. */
  mileage_required?: boolean;
  date: string;
  labors: MechWoLabor[];
  appointments?: WoAppointment[];
  my_running_timer: MechRunningTimer | null;
  server_now: string;
  /** Ревизия строк WO (labors_rev) и edit view этого открытия — у старых серверов нет. */
  labors_rev?: number;
  view_id?: string;
}

/** Для механиков /api/mobile/work_orders/<id> возвращает эту форму (без цен). */
export function fetchMechanicWoDetails(id: string, opts?: WoDetailsOptions) {
  return request<MechWorkOrderDetails>(woDetailsPath(id, opts), {}, opts?.timeoutMs || 0);
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
  /**
   * Итоговая ревизия WO после старта/стопа (у старых серверов нет). В ней
   * может быть и чужая правка, сделанная до таймера, — форма её не
   * «запоминает», а сразу сверяется с сервером (опрос ревизии).
   */
  labors_rev?: number;
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
  cross_refs?: PartCrossRef[];
  /** Единица измерения и минимальный остаток; у старых серверов полей нет (= each, минимум не задан). */
  unit_of_measure?: string;
  unit_short?: string;
  unit_label?: string;
  min_stock_enabled?: boolean;
  min_stock?: number;
  stock_state?: PartStockState;
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
  /** Рабочая ссылка (https:// дописан) и подпись без схемы; у старых серверов полей нет. */
  website_href?: string;
  website_label?: string;
  /** Только с правом vendors.view_credentials. Сам пароль здесь не приходит никогда. */
  portal_login?: string;
  has_portal_password?: boolean;
}

export function fetchVendorFull(id: string) {
  return request<{ ok: boolean; item: VendorFull }>(`/vendors/api/${encodeURIComponent(id)}`).then(
    (d) => d.item
  );
}

/** Учётка портала вендора с расшифрованным паролем (право vendors.view_credentials;
 *  каждый показ сохранённого пароля пишется в Activity Journal). */
export interface VendorCredentials {
  ok: boolean;
  website: string;
  website_href: string;
  login: string;
  password: string;
}

export function fetchVendorCredentials(id: string) {
  return request<VendorCredentials>(`/vendors/api/${encodeURIComponent(id)}/credentials`);
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

/** Учётка портала в сохранении вендора (сервер учитывает только при праве
 *  vendors.view_credentials): нет portal_login — логин не меняется, пустой
 *  portal_password — сохранённый остаётся, portal_password_clear — удалить. */
export interface VendorCredentialsPayload {
  portal_login?: string;
  portal_password?: string;
  portal_password_clear?: boolean;
}

export function updateVendor(id: string, payload: VendorPayload & VendorCredentialsPayload) {
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

// ── VIN scanning (баркод/фото) ──────────────────────────────────────

/** Сырая строка со сканера → валидный 17-символьный VIN или null.
 *  VIN-баркоды (Code 39) часто несут ведущий служебный "I" — срезаем. */
export function normalizeVin(raw: string): string | null {
  let s = (raw || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  if (s.length === 18 && "IOQ".includes(s[0])) s = s.slice(1);
  if (s.length !== 17 || /[IOQ]/.test(s)) return null;
  return s;
}

export interface VinMatch {
  unit_id: string;
  unit_label: string;
  customer_id: string;
  customer_label: string;
  is_system_customer: boolean;
}

export interface VinResolveResult {
  ok: boolean;
  vin: string;
  /** true — юнита не было, создан автоматически под "NEW Customer". */
  created: boolean;
  matches: VinMatch[];
}

export function resolveVin(vin: string) {
  return request<VinResolveResult>("/api/mobile/vin/resolve", {
    method: "POST",
    body: JSON.stringify({ vin }),
  });
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
  alternates?: PartSearchItem[]; // взаимозаменяемые парты (cross references)
  alt_for?: string; // клиентская метка: номер парта, чьей альтернативой является строка
}

export function searchParts(q: string) {
  return request<{ items: PartSearchItem[] }>(
    `/work_orders/api/parts/search?q=${encodeURIComponent(q)}`
  ).then((d) => d.items || []);
}

// Разворачивает alternates в плоский список для пикеров: альтернатива идёт
// строкой сразу под своим результатом с пометкой alt_for; дубли убираются.
export function flattenPartAlternates(items: PartSearchItem[]): PartSearchItem[] {
  const out: PartSearchItem[] = [];
  const seen = new Set(items.map((i) => i.id));
  for (const it of items) {
    out.push(it);
    for (const alt of it.alternates || []) {
      if (seen.has(alt.id)) continue;
      seen.add(alt.id);
      out.push({ ...alt, alt_for: it.part_number });
    }
  }
  return out;
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
  /** Ключ новой строки (newClientKey) — пока у неё нет labor_id. */
  client_key?: string;
  description: string;
  hours: string;
  rate_code: string;
  labor_total?: number | null;
  issue_description?: string;
  // Работа добавлена из пресета: сервер берёт часы/ставку из него
  // (часы механика-таймера тогда на биллинг не влияют).
  preset_id?: string;
  parts: WoFormPart[];
}

export interface WoSaveResult {
  ok: boolean;
  id: string;
  wo_number?: number;
  status: string;
  grand_total?: number | null; // отсутствует (или null) в ответах механик-эндпоинтов
  inventory_warnings: string[];
  // ── Протокол слияния (у старых серверов этих полей нет) ──
  /** Edit view сохранения (что клиент отправил) — base следующего сохранения. */
  view_id?: string;
  /** Edit view записанного состояния; равна view_id, если чужого ничего не слили. */
  merged_view_id?: string;
  /** Ревизия WO после этого сохранения. */
  labors_rev?: number;
  /** id строк — 1:1 со строками запроса. */
  labor_ids?: string[];
  /** id новых строк по их client_key. */
  client_keys?: Record<string, string>;
  /** Сервер слил сохранение с чужими правками. */
  merged_foreign?: boolean;
  /** Короткие заметки, что сделали с чужими правками (до 6). */
  merge_notes?: string[];
  /** Сохранено, но побочное действие (склад, коры, уведомления) не удалось. */
  warnings?: string[];
  /** Сохранение механика: строки WO в форме деталей механика (с id и временем). */
  labors?: MechWoLabor[];
}

export function createWorkOrder(payload: {
  customer_id: string;
  unit_id: string;
  status: string;
  labors: WoFormLabor[];
  mechanic_state?: "in_progress" | "done";
  unit_mileage?: string | number;
  /** WO из записи календаря: сервер привяжет запись к WO (и к уже идущему WO юнита). */
  calendar_event_id?: string;
}) {
  return request<WoSaveResult>("/api/mobile/work_orders", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function editWorkOrder(
  id: string,
  payload: {
    status?: string;
    labors: WoFormLabor[];
    mechanic_state?: "in_progress" | "done";
    unit_mileage?: string | number;
    /** 1 — сервер сливает сохранение с тем, что форма видела (view_id). */
    merge_protocol?: 1;
    /** Edit view, с которой форма начала правку (из деталей или прошлого сохранения). */
    view_id?: string;
  }
) {
  // Таймаут: повтор безопасен (client_key у новых строк, тот же view_id).
  return request<WoSaveResult>(
    `/api/mobile/work_orders/${encodeURIComponent(id)}`,
    { method: "POST", body: JSON.stringify(payload) },
    SAVE_TIMEOUT_MS
  );
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

export function sendWoAuthorization(
  woId: string,
  email: string,
  scope: "work_order" | "labor" = "work_order",
  laborIndex?: number,
  /** labor_id работы — сервер, который его понимает, найдёт работу по нему. */
  laborId?: string
) {
  const body: Record<string, unknown> = { email, scope };
  if (scope === "labor") {
    body.labor_index = laborIndex;
    if (laborId) body.labor_id = laborId;
  }
  return request<{ ok: boolean }>(
    `/work_orders/api/work_orders/${encodeURIComponent(woId)}/send-authorization`,
    { method: "POST", body: JSON.stringify(body) }
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

/** Менеджер подтверждает работу механика (или снимает подтверждение). */
export function setWorkOrderConfirmed(id: string, confirmed: boolean) {
  return request<{ ok: boolean; manager_confirmed: boolean }>(
    `/work_orders/api/work_orders/${encodeURIComponent(id)}/confirm`,
    { method: "POST", body: JSON.stringify({ confirmed }) }
  );
}

// ── Глобальный поиск юнитов (для механика в списке WO) ──────────────

export interface UnitSearchRow {
  id: string;
  label: string;
  unit_number: string;
  vin: string;
  customer_id: string;
  customer_label: string;
}

export function searchUnits(q: string, page = 1) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  params.set("page", String(page));
  return request<ListResponse<UnitSearchRow>>(`/api/mobile/units?${params.toString()}`);
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
  is_return?: boolean;
  return_for_order_number?: number | string | null;
}

export function fetchPartsOrders(q: string, page: number) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  params.set("page", String(page));
  return request<ListResponse<PartsOrderRow>>(`/api/mobile/parts_orders?${params.toString()}`);
}

export interface PartsOrderDetail {
  id: string;
  order_number?: number | string | null;
  vendor_id: string;
  status: string;
  vendor_bill: string;
  order_date: string;
  created_at: string | null;
  received_at: string | null;
  is_return?: boolean;
  return_for_order_number?: number | string | null;
  credit_total?: number;
  notes?: string;
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

// ── Vendor returns (возвраты вендору) ───────────────────────────────

export interface ReturnContextItem {
  part_id: string;
  part_number: string;
  description: string;
  price: number;
  ordered: number;
  returned: number;
  returnable: number;
}

export function fetchReturnContext(orderId: string) {
  return request<{ ok: boolean; order_number: number | string; items: ReturnContextItem[] }>(
    `/parts/api/orders/${encodeURIComponent(orderId)}/return-context`
  );
}

export function createOrderReturn(
  orderId: string,
  items: { part_id: string; quantity: number }[],
  notes: string
) {
  return request<{ ok: boolean; return_id: string; order_number: number; credit_total: number }>(
    `/parts/api/orders/${encodeURIComponent(orderId)}/returns`,
    { method: "POST", body: JSON.stringify({ items, notes }) }
  );
}

// ── Stocktakes (инвентаризация) ─────────────────────────────────────

export interface StocktakeRow {
  id: string;
  number: number | string | null;
  name: string;
  status: string;
  scope_location: string;
  scope_category: string;
  items_total: number;
  items_counted: number;
  items_adjusted: number;
  shortage_value: number;
  overage_value: number;
  created_label: string;
}

export function fetchStocktakes(q: string, page: number) {
  return request<ListResponse<StocktakeRow>>(`/api/mobile/stocktakes?${listQuery(q, page)}`);
}

export interface StocktakeLocationOption {
  id: string;
  path: string;
  depth: number;
}

export function fetchStocktakeOptions() {
  return request<{
    ok: boolean;
    locations: StocktakeLocationOption[];
    categories: { id: string; name: string }[];
  }>("/api/mobile/stocktake_options");
}

export function createStocktake(payload: { name: string; location_id: string; category_id: string }) {
  return request<{ ok: boolean; id: string; number: number }>("/api/mobile/stocktakes", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export interface StocktakeItemRow {
  id: string;
  part_number: string;
  description: string;
  location_path: string;
  expected: number;
  counted_qty: number | null;
  variance: number | null;
  variance_value: number | null;
  status: string;
  needs_recount: boolean;
  auto_zeroed: boolean;
}

export interface StocktakeDetail {
  ok: boolean;
  id: string;
  number: number | string | null;
  name: string;
  status: string;
  scope_location: string;
  scope_category: string;
  created_label: string;
  items_total: number;
  items_counted: number;
  discrepancies: number;
  variance_value: number;
  totals: {
    items_adjusted: number;
    items_zeroed: number;
    shortage_value: number;
    overage_value: number;
  } | null;
  items: StocktakeItemRow[];
}

export function fetchStocktakeDetail(id: string) {
  return request<StocktakeDetail>(`/api/mobile/stocktakes/${encodeURIComponent(id)}`);
}

export function countStocktakeItem(stocktakeId: string, itemId: string, countedQty: number) {
  return request<{
    ok: boolean;
    item: {
      id: string;
      counted_qty: number;
      expected_at_count: number;
      variance: number;
      variance_value: number;
    };
  }>(`/parts/stocktakes/${encodeURIComponent(stocktakeId)}/count`, {
    method: "POST",
    body: JSON.stringify({ item_id: itemId, counted_qty: countedQty }),
  });
}

export function completeStocktake(id: string, zeroUncounted: boolean) {
  return request<{ ok: boolean }>(`/parts/stocktakes/${encodeURIComponent(id)}/complete`, {
    method: "POST",
    body: JSON.stringify({ zero_uncounted: zeroUncounted }),
  });
}

export function cancelStocktake(id: string) {
  return request<{ ok: boolean }>(`/parts/stocktakes/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

// ── Calendar ────────────────────────────────────────────────────────

/**
 * Событие календаря. Два вида (как на вебе):
 *  - `appointment` — запись календаря (редактируется);
 *  - `work_order` — виртуальный блок WO (сессия таймера механика или чип
 *    «не расписан» на дате WO), `is_virtual`, только просмотр.
 * Время — настенные часы магазина без зоны ("2026-11-02T09:00:00").
 */
export interface CalendarEvent {
  id: string;
  kind: "appointment" | "work_order";
  is_virtual: boolean;
  editable: boolean;
  title: string;
  start_time: string;
  end_time: string;
  /** Статус записи; у WO-блока — то же, что wo_state. */
  status: string;
  customer_id: string;
  customer_label: string;
  unit_id: string;
  unit_label: string;
  mechanic_id: string;
  mechanic_name: string;
  mechanics: { id: string; name: string }[];
  mechanic_ids: string[];
  presets: { id: string; name: string }[];
  work_order_id: string;
  wo_number: number | null;
  wo_state: string;
  wo_status?: string;
  labor_summary: string[];
  labor_hours?: number;
  worked?: { id: string; name: string; seconds: number; running: boolean }[];
  running?: boolean;
  source?: "time_logs" | "work_order_date";
  /** WO без времени: чип на дате WO, на сетку не кладётся. */
  unscheduled?: boolean;
  /** План, перекрытый реальной работой по тому же WO — на сетке не рисуется. */
  superseded?: boolean;
}

export function fetchCalendarEvents(
  startIso: string,
  endIso: string,
  opts: { includeWorkOrders?: boolean } = {}
) {
  const params = new URLSearchParams({ start: startIso, end: endIso });
  if (opts.includeWorkOrders) params.set("include_work_orders", "1");
  return request<{ events?: CalendarEvent[]; items?: CalendarEvent[] } | CalendarEvent[]>(
    `/calendar/api/events?${params.toString()}`
  ).then((d: any) => (Array.isArray(d) ? d : d.events || d.items || []) as CalendarEvent[]);
}

export interface CalendarEventPayload {
  start_time: string;
  end_time: string;
  customer_id?: string;
  customer_label?: string;
  unit_id?: string;
  unit_label?: string;
  /** Бригада: все механики записи; mechanic_id/_name — первый (legacy). */
  mechanic_ids?: string[];
  mechanics?: { id: string; name: string }[];
  mechanic_id?: string;
  mechanic_name?: string;
  status?: string;
  title?: string;
  work_order_id?: string;
}

export function createCalendarEvent(payload: CalendarEventPayload) {
  return request<CalendarEvent>("/calendar/api/events", {
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

/** Статусы WO-блоков на календаре (фиксированные; механику paid не отдаётся). */
export function fetchCalendarWoStates() {
  return request<CalendarStatus[]>("/calendar/api/wo_states");
}

export function updateCalendarEvent(id: string, payload: Partial<CalendarEventPayload>) {
  return request<CalendarEvent>(`/calendar/api/events/${encodeURIComponent(id)}`, {
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

export function fetchAttachments(entityType: string, entityId: string, parentId?: string) {
  const params = new URLSearchParams({ entity_type: entityType, entity_id: entityId });
  if (parentId) params.set("parent_id", parentId);
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
  file: { uri: string; name: string; type: string },
  parentId?: string
) {
  const form = new FormData();
  form.append("entity_type", entityType);
  form.append("entity_id", entityId);
  if (parentId) form.append("parent_id", parentId);
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
