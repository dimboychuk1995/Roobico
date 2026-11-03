/**
 * Общие утилиты календаря: даты (без библиотек), раскладка пересекающихся
 * событий по колонкам, цвета и фильтры. Время событий — настенные часы
 * магазина без зоны; телефон считается в той же зоне (как и раньше).
 */
import { CalendarEvent, CalendarStatus } from "@/lib/api";

export type CalendarView = "schedule" | "day" | "3day" | "week" | "month";

export const VIEWS: { key: CalendarView; label: string; icon: string }[] = [
  { key: "schedule", label: "Schedule", icon: "list-outline" },
  { key: "day", label: "Day", icon: "calendar-clear-outline" },
  { key: "3day", label: "3 Day", icon: "albums-outline" },
  { key: "week", label: "Week", icon: "calendar-outline" },
  { key: "month", label: "Month", icon: "grid-outline" },
];

export const HOUR_H = 60;
export const GUTTER_W = 46;
export const DAY_MS = 24 * 60 * 60 * 1000;

export const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
/** Пн-первый порядок, как на веб-календаре. */
export const DAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"];
export const DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export const DAY_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

export function startOfWeek(d: Date): Date {
  const out = startOfDay(d);
  out.setDate(out.getDate() - ((out.getDay() + 6) % 7)); // понедельник
  return out;
}

export function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

export function addDays(d: Date, n: number): Date {
  const out = new Date(d);
  out.setDate(out.getDate() + n);
  return out;
}

export function addMonths(d: Date, n: number): Date {
  // День месяца зажимаем, чтобы 31 янв + 1 мес не перескочил в март.
  const first = new Date(d.getFullYear(), d.getMonth() + n, 1);
  const lastDay = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
  first.setDate(Math.min(d.getDate(), lastDay));
  return first;
}

export function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

export function sameMonth(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth();
}

/** Ключ дня "YYYY-MM-DD" в локальном времени. */
export function dayKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Индекс дня недели, Пн = 0. */
export function weekdayIndex(d: Date): number {
  return (d.getDay() + 6) % 7;
}

export function fmtTime(d: Date): string {
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function fmtMinutes(min: number): string {
  return `${String(Math.floor(min / 60)).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;
}

export function fmtDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  if (!h) return `${m}m`;
  return m ? `${h}h ${m}m` : `${h}h`;
}

/** "Mon, Nov 2" */
export function fmtDayShort(d: Date): string {
  return `${DAY_SHORT[weekdayIndex(d)]}, ${MONTHS[d.getMonth()].slice(0, 3)} ${d.getDate()}`;
}

export function parseEventDate(iso: string): Date {
  return new Date(iso);
}

export function eventStart(ev: CalendarEvent): Date {
  return parseEventDate(ev.start_time);
}

export function eventEnd(ev: CalendarEvent): Date {
  return parseEventDate(ev.end_time);
}

/** Дни, которые рисует вид. Месяц — 6 недель от понедельника недели 1-го числа. */
export function visibleDays(view: CalendarView, selected: Date): Date[] {
  switch (view) {
    case "day":
      return [startOfDay(selected)];
    case "3day":
      return [0, 1, 2].map((i) => addDays(startOfDay(selected), i));
    case "week": {
      const ws = startOfWeek(selected);
      return Array.from({ length: 7 }, (_, i) => addDays(ws, i));
    }
    case "month": {
      const gs = startOfWeek(startOfMonth(selected));
      return Array.from({ length: 42 }, (_, i) => addDays(gs, i));
    }
    case "schedule":
    default:
      return [startOfDay(selected)];
  }
}

/** Шаг навигации «вперёд/назад» для вида. */
export function shiftSelected(view: CalendarView, selected: Date, dir: 1 | -1): Date {
  switch (view) {
    case "day":
      return addDays(selected, dir);
    case "3day":
      return addDays(selected, 3 * dir);
    case "week":
      return addDays(selected, 7 * dir);
    case "month":
      return addMonths(selected, dir);
    case "schedule":
    default:
      return addDays(selected, 7 * dir);
  }
}

export interface DateRange {
  start: Date;
  end: Date; // исключительно
}

/** Диапазон данных для вида: всегда полные недели, чтобы точки на ленте/пикере не моргали. */
export function rangeForView(view: CalendarView, selected: Date, scheduleWeeks = 5): DateRange {
  if (view === "month") {
    const start = startOfWeek(startOfMonth(selected));
    return { start, end: addDays(start, 42) };
  }
  if (view === "schedule") {
    const start = startOfDay(selected);
    return { start, end: addDays(start, 7 * scheduleWeeks) };
  }
  const days = visibleDays(view, selected);
  const start = startOfWeek(days[0]);
  const last = days[days.length - 1];
  return { start, end: addDays(startOfWeek(last), 7) };
}

export function rangeCovers(outer: DateRange, inner: DateRange): boolean {
  return outer.start.getTime() <= inner.start.getTime() && outer.end.getTime() >= inner.end.getTime();
}

// ── раскладка ─────────────────────────────────────────────────────────

export interface PositionedEvent {
  ev: CalendarEvent;
  top: number;
  height: number;
  col: number;
  cols: number;
  startMin: number;
  endMin: number;
}

/**
 * Раскладка событий одного дня по колонкам (как в Google Calendar):
 * пересекающиеся события делят ширину, остальные занимают всю колонку.
 * События, выходящие за сутки, обрезаются по границам дня.
 */
export function layoutDayEvents(events: CalendarEvent[], day: Date): PositionedEvent[] {
  const dayStart = startOfDay(day).getTime();
  const dayEnd = dayStart + DAY_MS;
  const items = events
    .filter((e) => e.start_time && e.end_time)
    .map((e) => {
      const s = Math.max(dayStart, eventStart(e).getTime());
      const en = Math.min(dayEnd, eventEnd(e).getTime());
      const startMin = Math.floor((s - dayStart) / 60000);
      const endMin = Math.max(startMin + 20, Math.ceil((en - dayStart) / 60000));
      return { ev: e, startMin, endMin: Math.min(1440, endMin) };
    })
    .filter((it) => it.endMin > it.startMin)
    .sort((a, b) => a.startMin - b.startMin || b.endMin - a.endMin);

  const out: PositionedEvent[] = [];
  let cluster: typeof items = [];
  let clusterEnd = -1;

  const flush = () => {
    if (!cluster.length) return;
    // Жадное распределение по колонкам внутри кластера пересечений.
    const colEnds: number[] = [];
    const placed = cluster.map((it) => {
      let col = colEnds.findIndex((end) => end <= it.startMin);
      if (col === -1) {
        col = colEnds.length;
        colEnds.push(it.endMin);
      } else {
        colEnds[col] = it.endMin;
      }
      return { ...it, col };
    });
    const cols = colEnds.length;
    for (const p of placed) {
      out.push({
        ev: p.ev,
        top: (p.startMin / 60) * HOUR_H,
        height: Math.max(22, ((p.endMin - p.startMin) / 60) * HOUR_H - 2),
        col: p.col,
        cols,
        startMin: p.startMin,
        endMin: p.endMin,
      });
    }
    cluster = [];
    clusterEnd = -1;
  };

  for (const it of items) {
    if (cluster.length && it.startMin >= clusterEnd) flush();
    cluster.push(it);
    clusterEnd = Math.max(clusterEnd, it.endMin);
  }
  flush();
  return out;
}

// ── цвета / фильтры ──────────────────────────────────────────────────

export const DEFAULT_EVENT_COLOR = "#1a73e8";

export function makeColorOf(statuses: CalendarStatus[], woStates: CalendarStatus[]) {
  const st = new Map(statuses.map((s) => [s.key, s.color]));
  const wo = new Map(woStates.map((s) => [s.key, s.color]));
  return (ev: CalendarEvent): string => {
    if (ev.kind === "work_order") return wo.get(ev.wo_state) || DEFAULT_EVENT_COLOR;
    return st.get(ev.status) || DEFAULT_EVENT_COLOR;
  };
}

export function eventTitle(ev: CalendarEvent): string {
  if (ev.kind === "work_order") {
    return [ev.title || (ev.wo_number ? `WO #${ev.wo_number}` : "Work order"), ev.customer_label]
      .filter(Boolean)
      .join(" · ");
  }
  return ev.title || ev.customer_label || "Appointment";
}

export function isCancelled(ev: CalendarEvent): boolean {
  return ev.kind === "appointment" && ev.status === "cancelled";
}

export function isMine(ev: CalendarEvent, userId: string): boolean {
  if (!userId) return false;
  if (ev.mechanic_id === userId) return true;
  return (ev.mechanic_ids || []).includes(userId);
}

/** Событие попадает на день (с учётом событий через полночь). */
export function eventOnDay(ev: CalendarEvent, day: Date): boolean {
  if (!ev.start_time) return false;
  const ds = startOfDay(day).getTime();
  const de = ds + DAY_MS;
  const s = eventStart(ev).getTime();
  const e = ev.end_time ? eventEnd(ev).getTime() : s;
  return s < de && Math.max(e, s + 1) > ds;
}

/** Группировка по дням: ключ дня → события, отсортированные по старту. */
export function groupByDay(events: CalendarEvent[]): Map<string, CalendarEvent[]> {
  const map = new Map<string, CalendarEvent[]>();
  for (const ev of events) {
    if (!ev.start_time) continue;
    const k = dayKey(eventStart(ev));
    const arr = map.get(k);
    if (arr) arr.push(ev);
    else map.set(k, [ev]);
  }
  for (const arr of map.values()) {
    arr.sort((a, b) => a.start_time.localeCompare(b.start_time));
  }
  return map;
}

/** Читаемый контрастный текст поверх цвета статуса. */
export function textOn(hex: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return "#fff";
  const n = parseInt(m[1], 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return lum > 0.72 ? "#1a2e22" : "#fff";
}

// ── механики: цвета, инициалы, дорожки ──────────────────────────────

/** Палитра механиков — та же, что на веб-календаре (цвет = позиция в списке). */
export const MECHANIC_COLORS = [
  "#d81b60", "#f4511e", "#8d6e63", "#00acc1", "#c0ca33", "#6d4c41",
  "#ad1457", "#ff8f00", "#607d8b", "#9e9d24", "#e91e63", "#546e7a",
];
export const UNASSIGNED_KEY = "unassigned";

export interface Mechanic {
  id: string;
  name: string;
}

/** Дорожка внутри дня: механик магазина или «Unassigned». */
export interface Lane {
  key: string;
  name: string;
  color: string;
}

export function mechanicColor(id: string, mechanics: Mechanic[]): string {
  let idx = mechanics.findIndex((m) => String(m.id) === String(id));
  if (idx < 0) {
    // Нет в списке (деактивирован) — стабильный цвет по хэшу id.
    let h = 0;
    const s = String(id || "");
    for (let c = 0; c < s.length; c++) h = (h * 31 + s.charCodeAt(c)) & 0xffff;
    idx = h;
  }
  return MECHANIC_COLORS[idx % MECHANIC_COLORS.length];
}

export function mechanicInitials(name: string): string {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/** Механики события: mechanics[] с сервера либо одиночный mechanic_id. */
export function eventMechanics(ev: CalendarEvent): Mechanic[] {
  if (Array.isArray(ev.mechanics) && ev.mechanics.length) return ev.mechanics;
  if (ev.mechanic_id) return [{ id: ev.mechanic_id, name: ev.mechanic_name || "" }];
  return [];
}

/**
 * Ключи дорожек события: его механики из списка магазина; без механика
 * (или с деактивированным) — «Unassigned». Как на вебе.
 */
export function eventLaneKeys(ev: CalendarEvent, mechanics: Mechanic[]): string[] {
  const known = new Set(mechanics.map((m) => String(m.id)));
  const keys: string[] = [];
  for (const m of eventMechanics(ev)) {
    const id = String(m.id);
    if (known.has(id) && !keys.includes(id)) keys.push(id);
  }
  return keys.length ? keys : [UNASSIGNED_KEY];
}

/** Все механики магазина + «Unassigned», если среди событий есть блок без механика. */
export function buildLanes(mechanics: Mechanic[], events: CalendarEvent[], unassignedColor: string): Lane[] {
  const lanes: Lane[] = mechanics.map((m) => ({ key: String(m.id), name: m.name, color: mechanicColor(m.id, mechanics) }));
  if (events.some((e) => eventLaneKeys(e, mechanics).includes(UNASSIGNED_KEY))) {
    lanes.push({ key: UNASSIGNED_KEY, name: "Unassigned", color: unassignedColor });
  }
  return lanes;
}
