import Ionicons from "@expo/vector-icons/Ionicons";
import { Stack, useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  AppState,
  FlatList,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import * as ImagePicker from "expo-image-picker";

import { AttachmentsBlock } from "@/components/attachments-block";
import { fmtDayShort, fmtTime, parseEventDate } from "@/components/calendar/dates";
import { Field, SubmitButton } from "@/components/form";
import { LaborTimer } from "@/components/labor-timer";
import { SearchPickerModal } from "@/components/search-picker";
import { Badge, KV, RowCard } from "@/components/ui";
import { VinScannerModal } from "@/components/vin-scanner";
import { MileageGateModal } from "@/components/mileage-gate-modal";
import { WoAuthorizationModal } from "@/components/wo-authorization-modal";
import { useIsMechanic } from "@/context/auth";
import { useToast } from "@/context/toast";
import {
  ApiError,
  CustomerRow,
  LaborRate,
  MechLaborTime,
  MechWoLabor,
  MechWorkOrderDetails,
  PartSearchItem,
  PresetListItem,
  TimerResponse,
  VinMatch,
  WoAppointment,
  WoFormLabor,
  WoFormPart,
  WoRawLabor,
  WoSaveResult,
  WorkOrderDetails,
  REFRESH_TIMEOUT_MS,
  createWorkOrder,
  editWorkOrder,
  fetchCustomerDetails,
  fetchCustomers,
  fetchLaborRates,
  fetchMechanicWoDetails,
  fetchPartPrice,
  fetchPresetDetail,
  fetchPresets,
  fetchWoFormCustomers,
  fetchWoFormUnits,
  fetchWorkOrderDetails,
  fetchWorkOrderRev,
  flattenPartAlternates,
  money,
  newClientKey,
  parseHandwrittenWo,
  polishIssueText,
  resolveVin,
  fetchUnitActiveWorkOrder,
  UnitActiveWorkOrder,
  searchParts,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

interface UnitOption {
  id: string;
  label: string;
  /** Последний известный пробег юнита — подсказка в окне пробега. */
  mileage?: number | string | null;
}

function emptyLabor(): WoFormLabor {
  // client_key — постоянный ключ новой строки: повтор сохранения после
  // потерянного ответа сервер узнаёт по нему и не создаёт дубль.
  return { client_key: newClientKey(), description: "", hours: "", rate_code: "", labor_total: null, parts: [] };
}

/** Какие описания проблемы раскрыть при загрузке WO: все непустые. */
function openIssuesFor(items: { issue_description?: string | null }[]): Record<number, boolean> {
  const open: Record<number, boolean> = {};
  items.forEach((l, i) => {
    if ((l.issue_description || "").trim()) open[i] = true;
  });
  return open;
}

/** В строке есть что сохранять (только что добавленная пустая — нет). */
function laborHasContent(l: WoFormLabor): boolean {
  return !!(l.description.trim() || l.parts.length > 0 || (l.issue_description || "").trim());
}

/**
 * Пустой единственный лейбор заменяем, иначе добавляем новые. Сохранённую
 * строку (labor_id) не заменяем никогда: её пропажа из списка — удаление.
 */
function appendLabors(prev: WoFormLabor[], added: WoFormLabor[]): WoFormLabor[] {
  const only = prev.length === 1 && !prev[0].labor_id && !laborHasContent(prev[0]);
  return only ? added : [...prev, ...added];
}

/**
 * Строки к сохранению (протокол слияния): сохранённые (с labor_id) уходят
 * всегда, даже опустевшие, — сервер удаляет работу, только когда её убрали
 * из списка; новые — когда в них что-то есть, со своим client_key.
 */
function laborsToSend(items: WoFormLabor[]): WoFormLabor[] {
  return items
    .filter((l) => !!l.labor_id || laborHasContent(l))
    .map((l) => {
      const { client_key, ...rest } = l;
      return l.labor_id || !client_key ? rest : { ...rest, client_key };
    });
}

/** Строка механика из деталей или ответа сохранения (без денег). */
function mechLaborToForm(l: MechWoLabor): WoFormLabor {
  return {
    labor_id: l.labor_id,
    description: l.description,
    hours: "",
    rate_code: "",
    labor_total: null,
    issue_description: l.issue_description,
    parts: (l.parts || []).map((p) => ({
      part_id: p.part_id || "",
      part_number: p.part_number || "",
      description: p.description || "",
      qty: p.qty || 0,
      one_time_part: !!p.one_time_part,
    })),
  };
}

function mechLaborTimes(items: MechWoLabor[]): Record<string, MechLaborTime> {
  const times: Record<string, MechLaborTime> = {};
  items.forEach((l) => {
    if (l.labor_id) times[l.labor_id] = l.time;
  });
  return times;
}

/** Строка формы менеджера из raw_labors деталки. */
function rawLaborToForm(l: WoRawLabor): WoFormLabor {
  return {
    labor_id: l.labor_id || "",
    description: l.description,
    hours: l.hours,
    rate_code: l.rate_code,
    // Сохранённая база лейбора — чтобы правка не пересчитала
    // ручные суммы, введённые в вебе.
    labor_total: l.labor_base || null,
    issue_description: l.issue_description,
    parts: (l.parts || []).map((p) => ({
      part_id: p.part_id || "",
      part_number: p.part_number || "",
      description: p.description || "",
      qty: p.qty || 0,
      cost: p.cost || 0,
      price: p.price || 0,
      core_charge: p.core_charge || 0,
      misc_charge: p.misc_charge || 0,
      misc_charge_description: p.misc_charge_description || "",
      one_time_part: !!p.one_time_part,
    })),
  };
}

/**
 * Раскрытые описания проблемы после перечитывания WO: открытые раньше
 * строки — по labor_id, новые строки с описанием — открыты.
 */
function remapIssueOpen(
  prevLabors: WoFormLabor[],
  prevOpen: Record<number, boolean>,
  next: WoFormLabor[]
): Record<number, boolean> {
  const openIds = new Set<string>();
  const knownIds = new Set<string>();
  prevLabors.forEach((l, i) => {
    if (!l.labor_id) return;
    knownIds.add(l.labor_id);
    if (prevOpen[i]) openIds.add(l.labor_id);
  });
  const out: Record<number, boolean> = {};
  next.forEach((l, i) => {
    if (!l.labor_id) return;
    if (openIds.has(l.labor_id) || (!knownIds.has(l.labor_id) && (l.issue_description || "").trim())) out[i] = true;
  });
  return out;
}

/**
 * Ссылка на строку работы, которая переживает перестановку строк (опрос,
 * ответ сохранения кладут чужие работы раньше наших): labor_id, у новой —
 * client_key. index — только для строки без обоих ключей.
 */
interface LaborRef {
  labor_id: string;
  client_key: string;
  index: number;
}

function laborRefAt(rows: WoFormLabor[], idx: number): LaborRef {
  const l = rows[idx];
  return { labor_id: l?.labor_id || "", client_key: l?.client_key || "", index: idx };
}

/**
 * Где строка сейчас: по labor_id, по id, который сервер дал её client_key
 * (aliases — client_keys ответов сохранения), по client_key. -1 — строки
 * больше нет (удалили), чужую строку вместо неё не берём.
 */
function findLaborIndex(rows: WoFormLabor[], ref: LaborRef, aliases: Record<string, string>): number {
  const lid = ref.labor_id || (ref.client_key ? aliases[ref.client_key] || "" : "");
  if (lid) {
    const i = rows.findIndex((l) => l.labor_id === lid);
    if (i >= 0) return i;
  }
  if (ref.client_key) return rows.findIndex((l) => l.client_key === ref.client_key);
  if (lid) return -1;
  return ref.index < rows.length ? ref.index : -1;
}

/** id новых строк из ответа сохранения (client_keys) — на свои строки формы. */
function applyClientKeyIds(rows: WoFormLabor[], keyed: Record<string, string>): WoFormLabor[] {
  return rows.map((l) =>
    !l.labor_id && l.client_key && keyed[l.client_key] ? { ...l, labor_id: keyed[l.client_key] } : l
  );
}

// Defense-in-depth: механик не должен передавать деньги даже нулями —
// сервер их и так игнорирует, но и в трафике их быть не должно.
function stripMoney(items: WoFormLabor[]): WoFormLabor[] {
  return items.map(({ labor_total, ...l }) => ({
    ...l,
    hours: "",
    rate_code: "",
    parts: l.parts.map(({ cost, price, core_charge, misc_charge, ...p }) => p),
  }));
}

/**
 * Ключ сохранения механика — ровно то, что ушло бы на сервер: строки (без
 * денег), пробег, отметка. Совпал с ключом того, что на сервере уже есть
 * (насколько форма это знает), — автосейв запрос не шлёт.
 */
function saveKey(rows: WoFormLabor[], mileage: string, state: "in_progress" | "done"): string {
  return JSON.stringify([stripMoney(laborsToSend(rows)), String(mileage || "").trim(), state]);
}

/** Один и тот же пробег («123,456» и 123456)? */
function sameMileage(a: unknown, b: unknown): boolean {
  const norm = (v: unknown) => String(v ?? "").replace(/[\s,]/g, "");
  const x = norm(a);
  const y = norm(b);
  if (x === y) return true;
  if (!x || !y) return false;
  const nx = Number(x);
  const ny = Number(y);
  return Number.isFinite(nx) && Number.isFinite(ny) && nx === ny;
}

// Ответы сервера «WO закрыт для правки»: показываем текст сервера и не
// автосохраняем, пока замок не снимут.
const LOCK_ERROR_CODES = new Set(["wo_confirmed", "paid_cannot_edit", "estimate_web_only", "estimate_locked_for_mechanics"]);
const LOCKED_BY_MANAGER_TEXT = "A manager locked this work order — changes can't be saved right now.";
// Утверждение работы на закрытом WO, строки которого с тех пор менялись:
// номер работы на сервере не узнать — запрос не отправляем.
const LOCKED_CHANGED_AUTH_TEXT =
  "A manager locked this work order, and it changed since you opened it — the approval request was not sent. Ask a manager to send it.";
const ESTIMATE_LOCK_TEXT = "Estimates are edited in the web interface.";
// Опрос ревизии WO, пока форма на экране: коллеги, офис, таймеры.
const REV_POLL_MS = 20000;
const MANAGER_REV_NOTICE = "Someone else just updated this work order. When you save, their changes will be merged with yours.";

type SaveOutcome =
  | { ok: true; res: WoSaveResult }
  | { ok: false; reason: "skipped" | "locked" | "mileage" | "empty" | "error"; error?: unknown };

// Заметка записи такой длины становится самой строкой работы (как на вебе),
// длиннее — описанием проблемы.
const NOTE_AS_JOB_MAX_LEN = 200;

/** «Wed, Aug 5 · 14:00 – 16:00» — время записи настенное (магазина), как в календаре и в деталке WO. */
function appointmentWhen(a: WoAppointment): string {
  const start = parseEventDate(a.start_time || "");
  if (Number.isNaN(start.getTime())) return a.start_time || "";
  const end = parseEventDate(a.end_time || "");
  const range = Number.isNaN(end.getTime()) ? fmtTime(start) : `${fmtTime(start)} – ${fmtTime(end)}`;
  return `${fmtDayShort(start)} · ${range}`;
}

// Статусы записей настраиваются по магазину — тона только для дефолтных ключей.
const APPOINTMENT_TONES: Record<string, "success" | "warning" | "muted" | "danger" | "info"> = {
  scheduled: "info",
  confirmed: "success",
  in_progress: "warning",
  completed: "success",
  cancelled: "muted",
};

/** Ключ статуса записи → подпись («in_progress» → «In progress»). */
function appointmentStatusLabel(key: string): string {
  const s = String(key || "").replace(/_/g, " ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) : "";
}

export default function WorkOrderFormScreen() {
  const {
    id,
    customerId: presetCustomerId,
    customerLabel: presetCustomerLabel,
    unitId: presetUnitId,
    mileage: presetMileage,
    calendarEventId,
    presetIds: appointmentPresetIds,
    note: appointmentNote,
  } = useLocalSearchParams<{
    id?: string;
    customerId?: string;
    /** Имя клиента из записи календаря: у механика нет customers.view, деталку клиента он не загрузит. */
    customerLabel?: string;
    unitId?: string;
    mileage?: string;
    /** «Create work order» из записи календаря — после создания привязываем её к WO. */
    calendarEventId?: string;
    /** Пресеты записи (id через запятую) — офис применяет их в форме сам. */
    presetIds?: string;
    /** Заметка записи — в описание проблемы первой работы (без пресетов — сама работа). */
    note?: string;
  }>();
  const isEdit = !!id;
  const theme = useTheme();
  const toast = useToast();
  const router = useRouter();
  // Механик: без цен/часов/ставок, парты только part+qty, одна кнопка Save
  // (всегда in_progress) — сервер сам проставит цены и сохранит менеджерские поля.
  const isMechanic = useIsMechanic();

  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const [rates, setRates] = useState<LaborRate[]>([]);
  const [customer, setCustomer] = useState<{ id: string; label: string } | null>(null);
  const [unit, setUnit] = useState<UnitOption | null>(null);
  const [units, setUnits] = useState<UnitOption[]>([]);
  const [labors, setLabors] = useState<WoFormLabor[]>([emptyLabor()]);

  const [customerModal, setCustomerModal] = useState(false);
  const [unitModal, setUnitModal] = useState(false);
  const [vinScannerOpen, setVinScannerOpen] = useState(false);
  const [vinBusy, setVinBusy] = useState(false);
  // VIN найден на нескольких компаниях — механик выбирает, какую поставить.
  const [vinChoices, setVinChoices] = useState<VinMatch[] | null>(null);
  // Строка, для которой открыт выбор парта, — по ключу, не по номеру.
  const [partTarget, setPartTarget] = useState<LaborRef | null>(null);
  const [presetModal, setPresetModal] = useState(false);
  // Issue description свёрнуто по умолчанию — раскрывается по кнопке.
  const [issueOpen, setIssueOpen] = useState<Record<number, boolean>>({});
  const [polishing, setPolishing] = useState<LaborRef | null>(null);
  const [scanning, setScanning] = useState(false);

  // ── механик: всегда-edit вид (таймеры, фото, утверждение, автосейв) ──
  const [woMeta, setWoMeta] = useState<{
    wo_number: number | string | null;
    status: string;
    mechanic_done: boolean;
    customer_email: string;
  } | null>(null);
  // Записи календаря, привязанные к WO — сервер отдаёт их и механику (без денег).
  // В зависимости автосейва не входят: это только отображение.
  const [appointments, setAppointments] = useState<WoAppointment[]>([]);
  const [mileage, setMileage] = useState("");
  // Обязательный пробег: окно открыто, пока механик не подтвердит одометр.
  const [mileageGate, setMileageGate] = useState(false);
  const [lastKnownMileage, setLastKnownMileage] = useState("");
  const [laborTimes, setLaborTimes] = useState<Record<string, MechLaborTime>>({});
  const [serverOffsetMs, setServerOffsetMs] = useState(0);
  // Утверждение работы: работа — по labor_id, её номер на сервере ищется в
  // момент отправки (строки могли переставиться, пока окно открыто).
  const [authModal, setAuthModal] = useState<{ scope: "work_order" | "labor"; laborId?: string; jobLabel?: string } | null>(null);
  const [autoSaveState, setAutoSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [autoSaveError, setAutoSaveError] = useState("");
  // WO закрыт для правки механиком (подтверждён / смета / оплачен) — текст
  // сервера; автосейв стоит, пока замок не снимут.
  const [lockMessage, setLockMessage] = useState("");
  // Заметки сервера о слиянии с чужими правками (последнее сохранение).
  const [mergeNotes, setMergeNotes] = useState<string[]>([]);
  const loadedRef = useRef(false);
  const autoCreateFired = useRef(false);
  // Юнит, для которого уже проверено «есть ли WO в работе» (нет — можно создавать).
  const activeWoCheckedUnit = useRef("");
  const autosaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Структурные изменения (пресет, парты) сохраняем сразу, без debounce.
  const flushAutosave = useRef(false);
  // Пресеты/заметка записи календаря применяются к форме один раз.
  const appointmentPrefillDone = useRef(false);

  // ── несколько человек на одном WO (протокол слияния) ──────────────
  // Сохранение сливается на сервере трёхсторонне: base — edit view, с которой
  // форма начала правку (viewIdRef: из деталей, дальше — из ответа каждого
  // сохранения), current — WO в базе, mine — форма. Чужие работы, которых
  // форма не видела, не удаляются.
  const viewIdRef = useRef("");
  // Известная форме ревизия строк WO (labors_rev): опрос перечитывает WO,
  // только когда она ушла вперёд не нашими действиями.
  const revRef = useRef(0);
  // editSeq — счётчик правок пользователя (растёт синхронно в обработчиках);
  // baselineSeq — правки до этого номера уже совпадают с сервером
  // (загрузка, принятый ответ, успешное сохранение). Не равны — есть
  // несохранённые правки: их не затирает ни ответ сервера, ни опрос.
  const editSeq = useRef(0);
  const baselineSeq = useRef(0);
  // Сохранения механика идут строго по одному (цепочка промисов): автосейв,
  // Done и сохранение при уходе с экрана ждут запрос, который уже в полёте.
  const saveChain = useRef<Promise<unknown>>(Promise.resolve());
  const saveActive = useRef(0);
  // Автосейв уже стоит в очереди — новые правки он возьмёт сам (из рефов).
  const autosaveQueued = useRef(false);
  const autosaveFailed = useRef(false);
  // Ключ (saveKey) того, что на сервере, насколько форма знает: загрузка,
  // принятые строки сервера, успешное сохранение. Автосейв с тем же ключом
  // не шлётся (пустая новая строка, правка туда-обратно); принятая чужая
  // правка меняет ключ — возврат к своему прежнему значению снова уходит.
  const lastSentKey = useRef("");
  // Пробег, который сервер держит на WO (как форма его покажет), — для ключа.
  const serverMileageRef = useRef("");
  // labor_id строк WO в порядке сервера на ревизии revRef (загрузка, принятые
  // строки, своё сохранение без чужих правок); null — порядок неизвестен.
  // Нужен утверждению работы, когда детали WO механику уже закрыты.
  const serverOrderRef = useRef<string[] | null>(null);
  // client_key новой строки → её labor_id (client_keys ответов сохранения):
  // строку, принятую с сервера уже без client_key, находим по нему.
  const keyAliasRef = useRef<Record<string, string>>({});
  // Done уже сохранён: правки, успевшие после него (уход с экрана), уходят
  // тоже как done — иначе сохранение при уходе сняло бы отметку Done.
  const doneSent = useRef(false);
  const lockedRef = useRef(false);
  const revPollOff = useRef(false);
  const revCheckBusy = useRef(false);
  const noticeRev = useRef(0);
  const mountedRef = useRef(true);
  // Свежие значения для таймеров, очереди сохранений и ухода с экрана.
  // renderedSeq — номер правки, которому соответствуют laborsRef/mileageRef
  // (editSeq растёт в обработчике раньше, чем React отрисует правку):
  // сохранение отправляет ровно эту пару и не «засчитывает» правку, которой
  // ещё нет в отправленном списке.
  const laborsRef = useRef(labors);
  laborsRef.current = labors;
  const mileageRef = useRef(mileage);
  mileageRef.current = mileage;
  const renderedSeq = useRef(0);
  const mileageGateRef = useRef(mileageGate);
  mileageGateRef.current = mileageGate;

  const isPaid = woMeta?.status === "paid";
  const isPaidRef = useRef(isPaid);
  isPaidRef.current = isPaid;
  // editSeq объявлен выше: на момент отрисовки он ровно у отрисованных правок.
  renderedSeq.current = editSeq.current;
  // Открыто окно над конкретной строкой (выбор парта, отправка на
  // утверждение): строки с сервера (опрос, ответ сохранения) не принимаем,
  // пока его не закроют, — строки не переставятся под открытым окном.
  // Остальное (статус, замок) обновляется как обычно.
  const rowHoldRef = useRef(false);
  rowHoldRef.current = partTarget !== null || authModal?.scope === "labor";

  /** Номер строки сейчас (по ключу; -1 — строки уже нет). */
  const rowIndexOf = (rows: WoFormLabor[], ref: LaborRef) => findLaborIndex(rows, ref, keyAliasRef.current);
  // Спиннер AI-полировки — у той строки, где её запустили, даже если строки сдвинулись.
  const polishingIdx = polishing ? rowIndexOf(labors, polishing) : -1;

  // Правка формы пользователем (а не ответ сервера): новый номер правки.
  const editLabors = (fn: (prev: WoFormLabor[]) => WoFormLabor[]) => {
    editSeq.current += 1;
    setLabors(fn);
  };
  const editMileage = (value: string) => {
    editSeq.current += 1;
    setMileage(value);
  };
  const hasPendingEdits = () => editSeq.current !== baselineSeq.current;
  const learnRev = (rev: unknown) => {
    const n = Number(rev);
    if (rev !== undefined && rev !== null && Number.isFinite(n) && n > revRef.current) revRef.current = n;
  };

  // AI: фото бумажного WO → лейборы с запчастями (берём топ-кандидата).
  const scanPaperWo = async () => {
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    if (!perm.granted) {
      toast.show("Camera permission denied.", "error");
      return;
    }
    const res = await ImagePicker.launchCameraAsync({ quality: 0.8 });
    if (res.canceled || !res.assets?.[0]) return;
    const asset = res.assets[0];
    setScanning(true);
    try {
      const parsed = await parseHandwrittenWo({
        uri: asset.uri,
        name: asset.fileName || "wo.jpg",
        type: asset.mimeType || "image/jpeg",
      });
      const newLabors: WoFormLabor[] = [];
      let unmatched = 0;
      for (const l of parsed.labors || []) {
        const parts: WoFormPart[] = [];
        for (const p of l.parts || []) {
          const top = (p.candidates || [])[0];
          if (top && isMechanic) {
            // Механик цен не видит и не шлёт — сервер заполнит при сохранении.
            parts.push({
              part_id: top.part_id,
              part_number: top.part_number,
              description: top.description,
              qty: p.qty || 1,
            });
          } else if (top) {
            let price = top.selling_price || 0;
            if (!price) {
              try {
                price = (await fetchPartPrice(top.part_id, customer?.id || "")).price;
              } catch {
                price = top.average_cost || 0;
              }
            }
            parts.push({
              part_id: top.part_id,
              part_number: top.part_number,
              description: top.description,
              qty: p.qty || 1,
              cost: top.average_cost || 0,
              price,
            });
          } else {
            unmatched += 1;
            parts.push({
              part_id: "",
              part_number: p.written_part_number || "",
              description: p.written_description || "",
              qty: p.qty || 1,
              cost: 0,
              price: 0,
              one_time_part: true,
            });
          }
        }
        newLabors.push({
          client_key: newClientKey(),
          description: l.labor_description || "",
          hours: l.labor_hours != null ? String(l.labor_hours) : "",
          rate_code: rates[0]?.code || "",
          labor_total: null,
          parts,
        });
      }
      if (!newLabors.length) {
        toast.show("AI didn't find any labors on the photo.", "info");
        return;
      }
      editLabors((prev) => appendLabors(prev, newLabors));
      toast.show(
        `AI: ${newLabors.length} labor(s) recognized${unmatched ? `, ${unmatched} part(s) not matched — ${isMechanic ? "verify parts" : "check prices"}` : ""}.`,
        "success"
      );
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Scan failed.", "error");
    } finally {
      setScanning(false);
    }
  };

  // ── детали WO с сервера → форма ───────────────────────────────────
  /**
   * Строки, принятые с сервера (ответ сохранения или перечитанный WO), —
   * только когда в форме нет правок новее отправленных. Только что
   * добавленные пустые строки (на сервер не уходили) не теряем.
   */
  const adoptServerLabors = (next: WoFormLabor[]) => {
    serverOrderRef.current = next.map((l) => l.labor_id || "");
    const prevLabors = laborsRef.current;
    const pendingBlank = prevLabors.filter((l) => !l.labor_id && !laborHasContent(l));
    const merged = [...next, ...pendingBlank];
    const finalRows = merged.length ? merged : [emptyLabor()];
    setLabors(finalRows);
    // Сразу, не дожидаясь отрисовки: сохранение из очереди (Done следом за
    // автосейвом) должно отправить уже эти строки — base у него тоже новый.
    laborsRef.current = finalRows;
    setIssueOpen((prevOpen) => remapIssueOpen(prevLabors, prevOpen, finalRows));
    // Теперь на сервере — эти строки: с ними и сравнивается следующий
    // автосейв (вернуть своё значение поверх принятой чужой правки — правка).
    if (isMechanic) {
      lastSentKey.current = saveKey(finalRows, serverMileageRef.current, doneSent.current ? "done" : "in_progress");
    }
  };

  /** Ревизия и edit view из ответа сервера (у старых серверов их нет). */
  const takeServerView = (viewId: unknown, rev: unknown) => {
    if (typeof viewId === "string") viewIdRef.current = viewId;
    if (rev !== undefined && rev !== null && Number.isFinite(Number(rev))) revRef.current = Number(rev);
  };

  /** Замок WO для механика по деталям (оплаченный показывается отдельно). */
  const applyMechanicLock = (status: string, managerConfirmed: boolean) => {
    const estimate = status === "estimate";
    lockedRef.current = estimate || managerConfirmed;
    setLockMessage(estimate ? ESTIMATE_LOCK_TEXT : managerConfirmed ? LOCKED_BY_MANAGER_TEXT : "");
  };

  const applyMechanicDetails = (wo: MechWorkOrderDetails, initial: boolean) => {
    setCustomer({ id: wo.customer.id, label: wo.customer.label });
    setUnit({ id: wo.unit.id, label: wo.unit.label });
    setWoMeta({
      wo_number: wo.wo_number,
      status: wo.status,
      mechanic_done: !!wo.mechanic_done,
      customer_email: wo.customer_email || "",
    });
    setAppointments(Array.isArray(wo.appointments) ? wo.appointments : []);
    if (initial) {
      const lastKnown = wo.unit.mileage != null && wo.unit.mileage !== "" ? String(wo.unit.mileage) : "";
      // Пробег, только что подтверждённый при создании WO (router.replace
      // перемонтирует форму) — второй раз не спрашиваем.
      const confirmed = String(presetMileage || "").trim();
      const shown = confirmed || lastKnown;
      const woMileage = wo.wo_mileage != null ? String(wo.wo_mileage).trim() : "";
      setMileage(shown);
      setLastKnownMileage(lastKnown);
      // Что сервер уже держит на WO: тот же пробег в поле — не правка.
      serverMileageRef.current = sameMileage(shown, woMileage) ? shown : woMileage;
      // Сначала пробег, потом работа: сервер отклонит сохранение без него.
      if (wo.mileage_required && wo.status !== "paid" && !confirmed) setMileageGate(true);
      if (confirmed && !sameMileage(confirmed, woMileage)) {
        // Пробег подтверждён до перехода на этот WO (по юниту уже шёл WO —
        // сервер ответил 409 и форма открыла его) и на сервер не попал:
        // это правка — уйдёт первым автосейвом, даже без других изменений.
        editSeq.current += 1;
        flushAutosave.current = true;
      }
    }
    setLaborTimes(mechLaborTimes(wo.labors));
    const t = Date.parse(wo.server_now);
    if (Number.isFinite(t)) setServerOffsetMs(t - Date.now());
    if (initial) {
      const initialRows = wo.labors.map(mechLaborToForm);
      serverOrderRef.current = initialRows.map((l) => l.labor_id || "");
      if (initialRows.length) setLabors(initialRows);
      // Описания проблемы (в т.ч. заметка записи календаря, которую
      // сервер положил в первую работу) видны сразу: после создания WO
      // форма перемонтируется (router.replace) со свёрнутыми полями.
      setIssueOpen(openIssuesFor(wo.labors));
      // Загруженное — это то, что на сервере: правка без содержимого (пустая
      // «Add labor») запроса не шлёт и отметку Done механика не снимает.
      lastSentKey.current = saveKey(initialRows, serverMileageRef.current, "in_progress");
    } else {
      adoptServerLabors(wo.labors.map(mechLaborToForm));
    }
    takeServerView(wo.view_id, wo.labors_rev);
    applyMechanicLock(wo.status, !!wo.manager_confirmed);
  };

  const applyManagerDetails = (wo: WorkOrderDetails, initial: boolean) => {
    setCustomer({ id: wo.customer_id, label: wo.cust_name });
    setUnit({ id: wo.unit_id, label: wo.unit_label });
    const raw = Array.isArray(wo.raw_labors) ? wo.raw_labors : [];
    if (initial) {
      if (raw.length) {
        setLabors(raw.map(rawLaborToForm));
        setIssueOpen(openIssuesFor(raw));
      }
    } else {
      adoptServerLabors(raw.map(rawLaborToForm));
    }
    takeServerView(wo.view_id, wo.labors_rev);
  };

  // ── загрузка ──────────────────────────────────────────────────────
  useEffect(() => {
    (async () => {
      try {
        const rateList = await fetchLaborRates();
        setRates(rateList);

        if (isEdit && id && isMechanic) {
          // Механик получает безденежную форму деталей; сервер при сохранении
          // сольёт её с тем, что форма видела (view_id), и сохранит
          // менеджерские часы/ставки/цены. mp=1 — протокол слияния.
          const wo = await fetchMechanicWoDetails(id, { mergeProtocol: true });
          applyMechanicDetails(wo, true);
        } else if (isEdit && id) {
          const wo = await fetchWorkOrderDetails(id, { mergeProtocol: true });
          applyManagerDetails(wo, true);
        } else if (presetCustomerId) {
          // Механик без customers.view деталку клиента не получит: юниты —
          // из WO-эндпоинта, имя клиента — параметром из записи календаря.
          let opts: UnitOption[];
          if (isMechanic) {
            setCustomer({ id: presetCustomerId, label: String(presetCustomerLabel || "").trim() || "Customer" });
            opts = await fetchWoFormUnits(presetCustomerId);
          } else {
            const c = await fetchCustomerDetails(presetCustomerId);
            setCustomer({ id: c.id, label: c.label });
            opts = c.units.map((u) => ({ id: u.id, label: u.label, mileage: u.mileage ?? null }));
          }
          setUnits(opts);
          // Переход из общего поиска юнитов: юнит уже выбран.
          if (presetUnitId) {
            const preset = opts.find((u) => u.id === presetUnitId);
            if (preset) setUnit(preset);
          }
          // Переход из записи календаря (офис): пресеты записи — строками
          // работ, заметка — в описание проблемы. Механику не подставляем
          // ничего: его работы из записи собирает сервер при создании WO с
          // calendar_event_id (иначе автосейв продублировал бы их).
          if (!isMechanic && !isEdit && !appointmentPrefillDone.current) {
            appointmentPrefillDone.current = true;
            await applyAppointmentPrefill(presetCustomerId);
          }
        }
      } catch (e) {
        if (isMechanic && e instanceof ApiError && LOCK_ERROR_CODES.has(e.code)) {
          // Подтверждённый менеджером WO механику закрыт целиком.
          lockedRef.current = true;
          setLockMessage(e.message);
        }
        toast.show(e instanceof ApiError ? e.message : "Failed to load.", "error");
      } finally {
        // Автосейв включается только после начальной загрузки формы.
        loadedRef.current = true;
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Механик не имеет customers.view — юниты берём из WO-эндпоинта
  // (work_orders.create), как на веб-странице механика.
  const loadUnitsForCustomer = useCallback(
    (customerId: string) =>
      isMechanic
        ? fetchWoFormUnits(customerId)
        : fetchCustomerDetails(customerId).then((d) =>
            d.units.map((u) => ({ id: u.id, label: u.label, mileage: u.mileage ?? null }))
          ),
    [isMechanic]
  );

  const pickCustomer = async (c: CustomerRow) => {
    setCustomer({ id: c.id, label: c.company_name || c.contact_name || "—" });
    setUnit(null);
    setCustomerModal(false);
    try {
      setUnits(await loadUnitsForCustomer(c.id));
    } catch {
      setUnits([]);
    }
  };

  // ── VIN-скан: юнит+клиент подставляются по отсканированному VIN ───
  const applyVinMatch = async (m: VinMatch) => {
    setVinChoices(null);
    setCustomer({ id: m.customer_id, label: m.customer_label });
    // Юнит берём из свежего списка — там есть последний пробег для окна пробега.
    let fresh: UnitOption[] = [];
    try {
      fresh = await loadUnitsForCustomer(m.customer_id);
    } catch {
      fresh = [];
    }
    setUnits(fresh);
    setUnit(fresh.find((u) => u.id === m.unit_id) ?? { id: m.unit_id, label: m.unit_label });
  };

  const onVinScanned = async (vin: string) => {
    setVinScannerOpen(false);
    setVinBusy(true);
    try {
      const res = await resolveVin(vin);
      if (res.created) {
        // Юнита не было — создан автоматически под системным "NEW Customer";
        // офис позже перевесит его на настоящую компанию (Transfer на WO).
        toast.show(`VIN ${vin}: new unit created under "${res.matches[0].customer_label}".`, "success");
        await applyVinMatch(res.matches[0]);
      } else if (res.matches.length === 1) {
        toast.show(`VIN ${vin}: ${res.matches[0].unit_label} — ${res.matches[0].customer_label}.`, "success");
        await applyVinMatch(res.matches[0]);
      } else {
        setVinChoices(res.matches);
      }
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "VIN lookup failed.", "error");
    } finally {
      setVinBusy(false);
    }
  };

  // Возврат с экрана создания юнита: обновить список юнитов клиента и,
  // если появился ровно один новый, выбрать его автоматически.
  const refreshUnitsOnFocus = useCallback(() => {
    if (isEdit || !customer) return;
    loadUnitsForCustomer(customer.id)
      .then((fresh) => {
        setUnits((prev) => {
          if (!unit && fresh.length && fresh.length === prev.length + 1) {
            const known = new Set(prev.map((u) => u.id));
            const added = fresh.find((u) => !known.has(u.id));
            if (added) setUnit(added);
          }
          return fresh;
        });
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customer?.id, isEdit, unit, loadUnitsForCustomer]);

  useFocusEffect(refreshUnitsOnFocus);

  // ── лейборы ───────────────────────────────────────────────────────
  // Все правки строк — через editLabors: номер правки растёт синхронно, и
  // ни ответ сервера, ни опрос не затрут то, что ещё не сохранено.
  const patchLabor = (idx: number, patch: Partial<WoFormLabor>) => {
    editLabors((prev) => prev.map((l, i) => (i === idx ? { ...l, ...patch } : l)));
  };

  const removeLabor = (idx: number) => {
    flushAutosave.current = true;
    editLabors((prev) => (prev.length > 1 ? prev.filter((_, i) => i !== idx) : prev));
  };

  const patchPart = (laborIdx: number, partIdx: number, patch: Partial<WoFormPart>) => {
    editLabors((prev) =>
      prev.map((l, i) =>
        i === laborIdx
          ? { ...l, parts: l.parts.map((p, j) => (j === partIdx ? { ...p, ...patch } : p)) }
          : l
      )
    );
  };

  const removePart = (laborIdx: number, partIdx: number) => {
    flushAutosave.current = true;
    editLabors((prev) =>
      prev.map((l, i) =>
        i === laborIdx ? { ...l, parts: l.parts.filter((_, j) => j !== partIdx) } : l
      )
    );
  };

  /**
   * Правка строки, найденной по ключу, — для действий, которые кончаются не
   * сразу (выбор парта, подсчёт цены, AI): за это время строки могли
   * переставиться. false — строки уже нет, ничего не меняли.
   */
  const patchLaborByRef = (ref: LaborRef, fn: (l: WoFormLabor) => WoFormLabor): boolean => {
    if (rowIndexOf(laborsRef.current, ref) < 0) return false;
    editLabors((prev) => {
      const i = rowIndexOf(prev, ref);
      return i < 0 ? prev : prev.map((l, j) => (j === i ? fn(l) : l));
    });
    return true;
  };

  const addPart = async (item: PartSearchItem & { one_time?: boolean }) => {
    const target = partTarget;
    setPartTarget(null);
    if (!target) return;

    let newPart: WoFormPart;
    if (item.one_time) {
      // Нет в каталоге: строка без part_id, цену заполняет менеджер.
      newPart = {
        part_id: "",
        part_number: item.part_number,
        description: "",
        qty: 1,
        one_time_part: true,
        ...(isMechanic ? {} : { cost: 0, price: 0 }),
      };
    } else if (isMechanic) {
      // Цены заполнит сервер при сохранении; part_price для механика закрыт.
      newPart = {
        part_id: item.id,
        part_number: item.part_number,
        description: item.description,
        qty: 1,
      };
    } else {
      let price = item.average_cost ?? 0;
      let coreCharge = 0;
      try {
        const p = await fetchPartPrice(item.id, customer?.id || "");
        price = p.price;
        coreCharge = p.core_charge;
      } catch {
        // остаётся average_cost
      }
      newPart = {
        part_id: item.id,
        part_number: item.part_number,
        description: item.description,
        qty: 1,
        cost: item.average_cost ?? 0,
        price,
        core_charge: coreCharge,
      };
    }
    if (patchLaborByRef(target, (l) => ({ ...l, parts: [...l.parts, newPart] }))) {
      flushAutosave.current = true;
    } else {
      toast.show("That job was removed by someone else — the part was not added.", "error");
    }
  };

  // ── пресеты ───────────────────────────────────────────────────────
  /**
   * Строка работы из пресета. Механик: только парт + qty, часы/ставка пустые
   * (сервер по preset_id берёт их из пресета и сам проставляет цены); офис:
   * часы/ставка пресета, парты по закреплённой цене пресета, иначе по
   * прайсинг-матрице клиента. quiet — ошибку тостом не показывать (пакетное
   * применение из записи календаря даёт один общий тост). null — пресет не
   * загрузился.
   */
  const buildLaborFromPreset = async (
    presetId: string,
    customerId: string,
    opts: { quiet?: boolean } = {}
  ): Promise<WoFormLabor | null> => {
    try {
      const preset = await fetchPresetDetail(presetId);
      const parts: WoFormPart[] = [];
      for (const p of preset.parts || []) {
        if (isMechanic) {
          // Только парт и количество — цены проставит сервер при сохранении.
          parts.push({
            part_id: p.part_id || "",
            part_number: p.part_number,
            description: p.description,
            qty: p.qty || 1,
          });
          continue;
        }
        let price = p.price;
        if (price == null) {
          // Нет фиксированной цены — считаем по прайсинг-матрице клиента.
          try {
            const priced = await fetchPartPrice(p.part_id, customerId);
            price = priced.price;
          } catch {
            price = p.cost;
          }
        }
        parts.push({
          part_id: p.part_id || "",
          part_number: p.part_number,
          description: p.description,
          qty: p.qty || 1,
          cost: p.cost || 0,
          price: price || 0,
          core_charge: p.core_has_charge ? p.core_cost || 0 : 0,
        });
      }
      return {
        client_key: newClientKey(),
        description: preset.name || preset.description || "",
        hours: isMechanic ? "" : preset.labor_hours != null ? String(preset.labor_hours) : "",
        rate_code: isMechanic ? "" : preset.labor_rate_code || "",
        labor_total: null,
        // Сервер по preset_id берёт часы/ставку пресета для работы механика.
        preset_id: presetId,
        parts,
      };
    } catch (e) {
      if (!opts.quiet) toast.show(e instanceof ApiError ? e.message : "Failed to load preset.", "error");
      return null;
    }
  };

  const applyPreset = async (item: PresetListItem) => {
    setPresetModal(false);
    const newLabor = await buildLaborFromPreset(item.id, customer?.id || "");
    if (!newLabor) return;
    flushAutosave.current = true;
    editLabors((prev) => appendLabors(prev, [newLabor]));
    toast.show(`Preset "${item.name}" applied.`, "success");
  };

  /**
   * Переход из записи календаря (офис): каждый пресет записи — строка
   * работы, заметка — описание проблемы первой строки. Без пресетов короткая
   * заметка становится самой работой, длинная — описанием проблемы пустой
   * строки. Один тост на всё.
   */
  const applyAppointmentPrefill = async (customerId: string) => {
    const ids = String(appointmentPresetIds || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const note = String(appointmentNote || "").trim();
    if (!ids.length && !note) return;
    const built: WoFormLabor[] = [];
    for (const pid of ids) {
      // Последовательно: цены партов считает сервер, запрос на каждый парт.
      const labor = await buildLaborFromPreset(pid, customerId, { quiet: true });
      if (labor) built.push(labor);
    }
    if (built.length) {
      if (note) built[0] = { ...built[0], issue_description: note };
      editLabors((prev) => appendLabors(prev, built));
      setIssueOpen({ 0: !!note });
      const missing = ids.length - built.length;
      toast.show(
        `${built.length} preset(s) from the appointment applied${missing ? `, ${missing} could not be loaded` : ""}.`,
        missing ? "info" : "success"
      );
      return;
    }
    if (note) {
      const asJob = note.length <= NOTE_AS_JOB_MAX_LEN;
      editLabors((prev) =>
        appendLabors(prev, [asJob ? { ...emptyLabor(), description: note } : { ...emptyLabor(), issue_description: note }])
      );
      setIssueOpen({ 0: !asJob });
    }
    if (ids.length) toast.show("Could not load the appointment presets — add labors manually.", "error");
  };

  // ── AI-полировка описания проблемы ────────────────────────────────
  const polishIssue = async (idx: number) => {
    const text = (labors[idx]?.issue_description || "").trim();
    if (!text) {
      toast.show("Write the issue description first.", "error");
      return;
    }
    // Строка — по ключу: пока работает AI, опрос или ответ сохранения могли
    // переставить строки (текст не должен лечь в чужую работу).
    const target = laborRefAt(labors, idx);
    setPolishing(target);
    try {
      const res = await polishIssueText(text);
      if (patchLaborByRef(target, (l) => ({ ...l, issue_description: res.polished }))) {
        toast.show("Description polished by AI.", "success");
      } else {
        toast.show("That job was removed by someone else — the polished text was not applied.", "error");
      }
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "AI polish failed.", "error");
    } finally {
      setPolishing(null);
    }
  };

  // ── предварительный подсчёт (финально считает сервер + налог) ─────
  const estimate = useMemo(() => {
    if (isMechanic) return { laborSum: 0, partsSum: 0, total: 0 };
    const rateMap = new Map(rates.map((r) => [r.code, r.hourly_rate ?? 0]));
    let laborSum = 0;
    let partsSum = 0;
    for (const l of labors) {
      const manual = Number(l.labor_total);
      if (Number.isFinite(manual) && manual > 0) {
        laborSum += manual;
      } else {
        const h = parseFloat(l.hours || "0");
        laborSum += (Number.isFinite(h) ? h : 0) * (rateMap.get(l.rate_code) || 0);
      }
      for (const p of l.parts) partsSum += (p.qty || 0) * (p.price || 0);
    }
    return { laborSum, partsSum, total: laborSum + partsSum };
  }, [labors, rates, isMechanic]);

  // ── таймеры работ (механик, edit-вид) ─────────────────────────────
  const onTimerChanged = useCallback((res: TimerResponse) => {
    const t = Date.parse(res.server_now);
    if (Number.isFinite(t)) setServerOffsetMs(t - Date.now());
    setLaborTimes((prev) => {
      const next = { ...prev };
      Object.keys(res.time_summary || {}).forEach((lid) => {
        next[lid] = res.time_summary[lid];
      });
      return next;
    });
    // Старт всегда двигает ревизию WO, стоп — когда пересчитал часы, а ответ
    // отдаёт итоговую ревизию, в которой может быть и чужая правка, сделанная
    // раньше. Её не «запоминаем» (опрос решил бы, что форма свежая), а сразу
    // сверяемся с сервером: чистая форма перечитается, тихо — если видимые
    // строки не поменялись.
    void checkRevRef.current();
    // Старт таймера на сервере форсит in_progress и сбрасывает done.
    setWoMeta((m) => (m ? { ...m, status: "in_progress", mechanic_done: false } : m));
  }, []);

  const errorText = (e: unknown, fallback: string) =>
    e instanceof ApiError && e.message ? e.message : fallback;

  /** Подпись видимого содержимого строк: перечитанный WO что-то поменял? */
  const laborsSignature = (rows: WoFormLabor[]) =>
    JSON.stringify(
      rows.map((l) => [
        l.labor_id || "",
        l.description,
        l.issue_description || "",
        l.parts.map((p) => [p.part_id, p.part_number, p.description, p.qty]),
      ])
    );

  /**
   * Перечитать WO (опрос, замок, ответ без строк). Строки заменяются, только
   * если в форме нет правок новее сохранённых и, пока шёл запрос, их не
   * сделали; иначе механику обновляется лишь статус/замок. true — строки
   * заменены и их видимое содержимое поменялось.
   */
  const refreshFromServer = async (opts: { fromSave?: boolean } = {}): Promise<boolean> => {
    if (!isEdit || !id) return false;
    const seq = editSeq.current;
    const busyNow = () => !opts.fromSave && saveActive.current > 0;
    // Строки не заменяем: есть несохранённые правки (в т.ч. сделанные, пока
    // шёл запрос) или открыто окно над строкой (выбор парта, утверждение).
    const keepRows = () => editSeq.current !== seq || seq !== baselineSeq.current || busyNow() || rowHoldRef.current;
    // Пустые новые строки (на сервер не уходят) в сравнение не входят.
    const before = laborsSignature(laborsRef.current.filter((l) => !!l.labor_id || laborHasContent(l)));
    // С таймаутом: зависший запрос не должен навсегда занять опрос.
    const detailsOpts = { mergeProtocol: true, timeoutMs: REFRESH_TIMEOUT_MS };
    try {
      if (isMechanic) {
        const fresh = await fetchMechanicWoDetails(id, detailsOpts);
        if (!mountedRef.current) return false;
        if (keepRows()) {
          // Несохранённые правки не трогаем — только статус и замок.
          setWoMeta((m) => (m ? { ...m, status: fresh.status, mechanic_done: !!fresh.mechanic_done } : m));
          applyMechanicLock(fresh.status, !!fresh.manager_confirmed);
          return false;
        }
        applyMechanicDetails(fresh, false);
        return laborsSignature(fresh.labors.map(mechLaborToForm)) !== before;
      }
      const fresh = await fetchWorkOrderDetails(id, detailsOpts);
      if (!mountedRef.current || keepRows()) return false;
      applyManagerDetails(fresh, false);
      return laborsSignature((fresh.raw_labors || []).map(rawLaborToForm)) !== before;
    } catch (e) {
      if (isMechanic && e instanceof ApiError && LOCK_ERROR_CODES.has(e.code)) {
        lockedRef.current = true;
        setLockMessage(e.message);
      }
      return false;
    }
  };

  /** Сервер ответил «WO закрыт»: его текст, автосейв стоит, WO перечитываем. */
  const handleLockError = (e: ApiError) => {
    const firstTime = !lockedRef.current;
    lockedRef.current = true;
    setLockMessage(e.message || LOCKED_BY_MANAGER_TEXT);
    if (firstTime) toast.show(e.message || LOCKED_BY_MANAGER_TEXT, "error");
    if (autosaveTimer.current) {
      clearTimeout(autosaveTimer.current);
      autosaveTimer.current = null;
    }
    void refreshFromServer({ fromSave: true });
  };

  /** Заметки сервера о слиянии — плашкой над работами; сбои побочных действий — тостом. */
  const showAutosaveOutcome = (res: WoSaveResult) => {
    const notes = (res.merge_notes || []).map((n) => String(n || "").trim()).filter(Boolean);
    if (notes.length) setMergeNotes(notes);
    const warnings = (res.warnings || []).map((w) => String(w || "").trim()).filter(Boolean);
    if (warnings.length) toast.show(warnings.join(" "), "error");
  };

  /** Форма закрывается после сохранения — итог слияния показываем окном. */
  const alertSaveOutcome = (res: WoSaveResult) => {
    const lines = [...(res.merge_notes || []), ...(res.warnings || [])]
      .map((n) => String(n || "").trim())
      .filter(Boolean);
    if (!lines.length) return;
    const intro = res.merged_foreign
      ? "Other people changed this work order at the same time. Both sets of changes were kept:\n\n"
      : "";
    Alert.alert("Work order saved", intro + lines.map((n) => `• ${n}`).join("\n"));
  };

  // ── сохранение механика (edit-вид): автосейв, Done, уход с экрана ──
  /**
   * Одно сохранение механика. Читает только рефы (вызывается из очереди,
   * таймера и при уходе с экрана). Правок за время запроса не было и над
   * строкой не открыто окно — форма берёт строки из ответа (там чужие
   * изменения, слитые сервером, и id новых работ); иначе остаются свои,
   * новым строкам id ставятся по client_key, base следующего сохранения —
   * то, что отправили (view_id ответа).
   */
  const performMechanicSave = async (
    mechanicState: "in_progress" | "done",
    auto: boolean
  ): Promise<SaveOutcome> => {
    if (!isMechanic || !isEdit || !id) return { ok: false, reason: "skipped" };
    if (isPaidRef.current || lockedRef.current) return { ok: false, reason: "locked" };
    if (mileageGateRef.current) return { ok: false, reason: "mileage" };
    const mileageNow = mileageRef.current.trim();
    if (!mileageNow) {
      setMileageGate(true);
      return { ok: false, reason: "mileage" };
    }
    // Ровно отрисованное состояние и его номер правки (см. renderedSeq).
    const seq = renderedSeq.current;
    const sent = laborsRef.current;
    const rows = stripMoney(laborsToSend(sent));
    if (!rows.length) {
      // Только пустые новые строки — сохранять нечего.
      if (auto) baselineSeq.current = Math.max(baselineSeq.current, seq);
      return { ok: false, reason: "empty" };
    }
    const sentKey = saveKey(sent, mileageNow, mechanicState);
    if (auto && sentKey === lastSentKey.current) {
      // Ровно то, что на сервере уже есть (добавили пустую строку, правка
      // туда-обратно и т. п.).
      baselineSeq.current = Math.max(baselineSeq.current, seq);
      autosaveFailed.current = false;
      setAutoSaveState("saved");
      setAutoSaveError("");
      return { ok: false, reason: "skipped" };
    }

    if (auto) setAutoSaveState("saving");
    try {
      const res = await editWorkOrder(id, {
        status: "in_progress",
        labors: rows,
        mechanic_state: mechanicState,
        unit_mileage: mileageNow,
        merge_protocol: 1,
        view_id: viewIdRef.current,
      });
      autosaveFailed.current = false;
      // Сервер теперь держит этот пробег и отметку (ключ «уже сохранено»
      // ниже строится уже с ними).
      serverMileageRef.current = mileageNow;
      if (mechanicState === "done") doneSent.current = true;
      const keyed: Record<string, string> =
        res.client_keys && typeof res.client_keys === "object" ? res.client_keys : {};
      Object.assign(keyAliasRef.current, keyed);
      const adopt = editSeq.current === seq && !rowHoldRef.current;
      if (adopt && Array.isArray(res.labors)) {
        learnRev(res.labors_rev);
        adoptServerLabors(res.labors.map(mechLaborToForm));
        setLaborTimes(mechLaborTimes(res.labors));
        const view = res.merged_foreign ? res.merged_view_id || res.view_id : res.view_id;
        if (typeof view === "string") viewIdRef.current = view;
        baselineSeq.current = Math.max(baselineSeq.current, seq);
      } else if (
        adopt &&
        (res.merged_foreign || sent.some((l) => !l.labor_id && laborHasContent(l)))
      ) {
        // Ответ без строк (старый сервер / сбой сборки строк): id новых работ
        // и чужие правки — перечитыванием WO, как раньше. Порядок строк на
        // сервере узнаем из него же.
        learnRev(res.labors_rev);
        lastSentKey.current = sentKey;
        serverOrderRef.current = null;
        baselineSeq.current = Math.max(baselineSeq.current, seq);
        await refreshFromServer({ fromSave: true });
      } else {
        if (Object.keys(keyed).length) setLabors((prev) => applyClientKeyIds(prev, keyed));
        if (typeof res.view_id === "string") viewIdRef.current = res.view_id;
        if (res.merged_foreign) {
          // Сервер слил чужие правки, а форма их не показала: ревизию не
          // запоминаем — опрос перечитает WO, как только форма освободится;
          // и сравнивать «уже сохранено» не с чем, порядок строк неизвестен.
          lastSentKey.current = "";
          serverOrderRef.current = null;
        } else {
          learnRev(res.labors_rev);
          const savedRows = applyClientKeyIds(sent, keyed);
          lastSentKey.current = saveKey(savedRows, mileageNow, mechanicState);
          // Без чужих правок строки на сервере — отправленные, в том же
          // порядке (молча оставленные сервером — только в конце).
          serverOrderRef.current = laborsToSend(savedRows).map((l) => l.labor_id || "");
        }
        baselineSeq.current = Math.max(baselineSeq.current, seq);
      }
      setAutoSaveState("saved");
      setAutoSaveError("");
      setWoMeta((m) => (m ? { ...m, status: "in_progress", mechanic_done: mechanicState === "done" } : m));
      if (auto) showAutosaveOutcome(res);
      return { ok: true, res };
    } catch (e) {
      // Что теперь на сервере, неизвестно: таймаут или обрыв после отправки —
      // запрос мог и записаться (пробег сервер пишет каждым сохранением).
      // Следующий автосейв уходит всегда, даже если форма вернулась к
      // прежнему ключу (исправили опечатку обратно); повтор идемпотентен
      // (client_key, тот же view_id).
      lastSentKey.current = "";
      if (e instanceof ApiError && e.code === "mileage_required") setMileageGate(true);
      if (e instanceof ApiError && LOCK_ERROR_CODES.has(e.code)) {
        handleLockError(e);
      } else {
        // Повтор — следующей правкой или опросом (раз в 20 с).
        autosaveFailed.current = true;
      }
      if (auto) {
        setAutoSaveState("error");
        setAutoSaveError(errorText(e, "check connection."));
      }
      return { ok: false, reason: "error", error: e };
    }
  };
  const performRef = useRef(performMechanicSave);
  performRef.current = performMechanicSave;

  /** Сохранения механика — строго по одному: новое ждёт запрос в полёте. */
  const enqueueSave = (task: () => Promise<SaveOutcome>): Promise<SaveOutcome> => {
    saveActive.current += 1;
    const p = saveChain.current.then(task).finally(() => {
      saveActive.current -= 1;
    });
    saveChain.current = p.catch(() => undefined);
    return p;
  };

  /** Автосейв в очередь — один: стартуя, он возьмёт самое свежее состояние. */
  const queueAutosave = () => {
    if (autosaveQueued.current) return;
    autosaveQueued.current = true;
    enqueueSave(() => {
      autosaveQueued.current = false;
      return performRef.current(doneSent.current ? "done" : "in_progress", true);
    }).catch(() => undefined);
  };

  const scheduleAutosave = (delay: number) => {
    if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    autosaveTimer.current = setTimeout(() => {
      autosaveTimer.current = null;
      queueAutosave();
    }, delay);
  };

  // ── автосейв (механик, edit-вид): любое изменение → in_progress ───
  useEffect(() => {
    // Пока окно пробега открыто, ничего не сохраняем: первое сохранение
    // уйдёт уже с подтверждённым пробегом. Нет правок новее сохранённых
    // (открытие WO, ответ сервера, перечитывание) — сохранять нечего.
    if (!isMechanic || !isEdit || !loadedRef.current || isPaid || lockedRef.current || mileageGate) return;
    if (!hasPendingEdits()) return;
    const delay = flushAutosave.current ? 50 : 1200;
    flushAutosave.current = false;
    scheduleAutosave(delay);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [labors, mileage, mileageGate]);

  // Уход с экрана / приложение в фоне: несохранённые правки механика
  // сохраняем сразу — следом за запросом, который уже в полёте.
  const flushPendingSave = () => {
    if (autosaveTimer.current) {
      clearTimeout(autosaveTimer.current);
      autosaveTimer.current = null;
    }
    if (!isMechanic || !isEdit || !loadedRef.current) return;
    if (!hasPendingEdits()) return;
    queueAutosave();
  };
  const flushRef = useRef(flushPendingSave);
  flushRef.current = flushPendingSave;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      flushRef.current();
    };
  }, []);

  // ── свежесть формы: коллеги, офис, таймеры ────────────────────────
  /**
   * Опрос ревизии WO (на фокусе экрана, при возврате приложения и раз в
   * 20 с). Ревизия ушла вперёд не нами: форма без несохранённых правок —
   * перечитываем WO; с правками — механику их сольёт ближайший автосейв,
   * менеджеру — подсказка. Старый сервер без эндпоинта (404 not_found) —
   * опрос молча выключается.
   */
  const checkRev = async () => {
    if (!isEdit || !id || !loadedRef.current || revPollOff.current || revCheckBusy.current) return;
    if (AppState.currentState !== "active") return;
    // Сохранение в пути — его ответ сам обновит форму. Открыто окно над
    // строкой (выбор парта, утверждение) — подождём: строки под ним не меняем.
    if (saveActive.current > 0 || autosaveTimer.current || rowHoldRef.current) return;
    revCheckBusy.current = true;
    try {
      const info = await fetchWorkOrderRev(id);
      if (!mountedRef.current) return;
      if (isMechanic && !isPaidRef.current) {
        const lockedNow = info.locked === true;
        if (lockedNow !== lockedRef.current) {
          lockedRef.current = lockedNow;
          setLockMessage(lockedNow ? LOCKED_BY_MANAGER_TEXT : "");
          if (!hasPendingEdits()) {
            await refreshFromServer();
          } else if (!lockedNow) {
            // Замок сняли — несохранённые правки уходят сразу.
            scheduleAutosave(50);
          }
          return;
        }
      }
      if (isMechanic && hasPendingEdits() && autosaveFailed.current && !lockedRef.current) {
        // Автосейв не прошёл (сеть, WO занят) — пробуем снова.
        queueAutosave();
        return;
      }
      const rev = Number(info.labors_rev);
      if (!Number.isFinite(rev) || rev <= revRef.current) return;
      if (hasPendingEdits()) {
        if (!isMechanic && rev > noticeRev.current) {
          noticeRev.current = rev;
          toast.show(MANAGER_REV_NOTICE, "info");
        }
        return;
      }
      if (await refreshFromServer()) toast.show("Updated with changes from other people.", "info");
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        revPollOff.current = true;
        if (e.code === "work_order_not_found") toast.show("This work order was deleted.", "error");
      }
      // Сеть и прочее — молча, до следующего тика.
    } finally {
      revCheckBusy.current = false;
    }
  };
  const checkRevRef = useRef(checkRev);
  checkRevRef.current = checkRev;

  useFocusEffect(
    useCallback(() => {
      if (!isEdit || !id) return undefined;
      void checkRevRef.current();
      const handle = setInterval(() => {
        void checkRevRef.current();
      }, REV_POLL_MS);
      return () => clearInterval(handle);
    }, [isEdit, id])
  );

  useEffect(() => {
    if (!isEdit || !id) return undefined;
    const sub = AppState.addEventListener("change", (next) => {
      if (next === "active") void checkRevRef.current();
      else flushRef.current();
    });
    return () => sub.remove();
  }, [isEdit, id]);

  // ── сохранение ────────────────────────────────────────────────────
  // mechanicState: работа механика всегда сохраняется как in_progress —
  // "done" лишь помечает менеджеру, что механик закончил и ждёт проверки.
  const save = async (status: "open" | "in_progress", mechanicState?: "in_progress" | "done") => {
    if (!isEdit && (!customer || !unit)) {
      toast.show("Select customer and unit.", "error");
      return;
    }

    // Механик в существующем WO: то же сохранение, что автосейв (с отметкой
    // Done) — в очереди следом за запросом, который уже в полёте.
    if (isMechanic && isEdit && id) {
      if (!mileage.trim()) {
        setMileageGate(true);
        return;
      }
      // Явное сохранение отменяет запланированный автосейв.
      if (autosaveTimer.current) {
        clearTimeout(autosaveTimer.current);
        autosaveTimer.current = null;
      }
      setBusy(true);
      let outcome: SaveOutcome;
      try {
        outcome = await enqueueSave(() => performRef.current(mechanicState || "in_progress", false));
      } catch (e) {
        outcome = { ok: false, reason: "error", error: e };
      }
      setBusy(false);
      if (outcome.ok) {
        (outcome.res.inventory_warnings || []).forEach((w) => toast.show(w, "info"));
        toast.show("Work order saved.", "success");
        alertSaveOutcome(outcome.res);
        router.back();
      } else if (outcome.reason === "empty") {
        toast.show("Add at least one labor with a description.", "error");
      } else if (outcome.reason === "locked") {
        toast.show(isPaid ? "Paid work orders cannot be edited." : lockMessage || LOCKED_BY_MANAGER_TEXT, "error");
      } else if (outcome.reason === "error") {
        toast.show(errorText(outcome.error, "Save failed."), "error");
      }
      return;
    }

    // Сохранённые строки — всегда, новые — когда в них что-то есть.
    let validLabors = laborsToSend(labors);
    // Механик создаёт WO сразу при выборе клиента и юнита — работ ещё нет,
    // они добавятся автосейвом уже в edit-виде.
    if (!validLabors.length && !(isMechanic && !isEdit)) {
      toast.show("Add at least one labor with a description.", "error");
      return;
    }
    if (isMechanic) {
      if (!mileage.trim()) {
        setMileageGate(true);
        return;
      }
      validLabors = stripMoney(validLabors);
      status = "in_progress";
    }

    setBusy(true);
    // Опрос не перечитывает форму, пока её сохранение в пути.
    saveActive.current += 1;
    try {
      let res: WoSaveResult;
      const mechanicFields = isMechanic
        ? { mechanic_state: mechanicState || "in_progress", unit_mileage: mileage.trim() || undefined }
        : {};
      if (isEdit && id) {
        // Менеджер: сервер сольёт форму с тем, что она видела (view_id), —
        // работы, добавленные механиками после открытия формы, не удалятся.
        res = await editWorkOrder(id, {
          status,
          labors: validLabors,
          merge_protocol: 1,
          view_id: viewIdRef.current,
        });
      } else {
        res = await createWorkOrder({
          customer_id: customer!.id,
          unit_id: unit!.id,
          status,
          labors: validLabors,
          ...mechanicFields,
          // Запись календаря привязывается к WO на сервере (как на вебе) —
          // в т.ч. когда по юниту WO уже идёт и сервер отвечает 409.
          calendar_event_id: calendarEventId || undefined,
        });
      }
      (res.inventory_warnings || []).forEach((w) => toast.show(w, "info"));
      toast.show(
        isEdit ? "Work order saved." : `Work order #${res.wo_number} created.`,
        "success"
      );
      if (isEdit) alertSaveOutcome(res);
      if (!isEdit && isMechanic && res.id) {
        // Механик после создания сразу попадает в рабочий edit-вид WO —
        // с таймерами, фото и отправкой на утверждение.
        // Пробег уже подтверждён при создании — передаём, чтобы форма не спросила снова.
        router.replace({ pathname: "/work-order-form", params: { id: res.id, mileage: mileage.trim() } });
      } else {
        router.back();
      }
    } catch (e) {
      // Коллега уже завёл WO по этому юниту (гонка или старая проверка) —
      // сервер не даёт создать дубль и возвращает тот WO: открываем его.
      if (e instanceof ApiError && e.code === "unit_has_active_work_order" && e.data?.work_order?.id) {
        openExistingWorkOrder(e.data.work_order, e.message, mileage.trim());
        return;
      }
      // Провал авто-создания: разрешаем повторный триггер по выбору юнита.
      autoCreateFired.current = false;
      toast.show(e instanceof ApiError ? e.message : "Save failed.", "error");
    } finally {
      saveActive.current -= 1;
      setBusy(false);
    }
  };

  // Один юнит в цеху — один WO: по юниту уже идёт работа (другой механик
  // или просто in progress) → открываем тот WO вместо создания нового.
  const openExistingWorkOrder = (wo: UnitActiveWorkOrder, message?: string, confirmedMileage?: string) => {
    autoCreateFired.current = true;
    toast.show(
      `${message || `Work order #${wo.wo_number} is already in progress for this unit.`} Opening it…`,
      "info"
    );
    // Пробег, уже подтверждённый механиком перед созданием (путь через 409),
    // передаём в форму того WO — юнит тот же, второй раз не спрашиваем.
    router.replace({
      pathname: "/work-order-form",
      params: confirmedMileage ? { id: wo.id, mileage: confirmedMileage } : { id: wo.id },
    });
  };

  // Механик: WO создаётся сам, как только выбраны клиент и юнит — но сначала
  // проверяем, не идёт ли по юниту работа уже (VIN-скан, выбор руками,
  // переход из поиска юнитов — все пути сходятся сюда). Исключение — «Start
  // work» из записи календаря: запись привязывает к WO только сервер в POST
  // work_orders, в т.ч. когда по юниту WO уже идёт (409 — тот WO откроет
  // обработчик в save). Открыв его отсюда, по предварительной проверке, мы
  // оставили бы запись без привязки — поэтому проверку пропускаем.
  useEffect(() => {
    if (!isMechanic || isEdit || !customer || !unit || autoCreateFired.current) return;
    let cancelled = false;
    (async () => {
      if (!calendarEventId && activeWoCheckedUnit.current !== unit.id) {
        try {
          const res = await fetchUnitActiveWorkOrder(unit.id);
          if (cancelled) return;
          activeWoCheckedUnit.current = unit.id;
          if (res.work_order) {
            openExistingWorkOrder(res.work_order, res.message);
            return;
          }
        } catch {
          // Проверка не удалась — создание всё равно защищено сервером (409).
          if (cancelled) return;
        }
      }
      // Сначала пробег: WO создаётся уже с подтверждённым одометром.
      if (!mileage.trim()) {
        setMileageGate(true);
        return;
      }
      autoCreateFired.current = true;
      save("in_progress", "in_progress");
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customer, unit, mileage]);

  /**
   * Номер работы в WO на сервере — в момент отправки на утверждение: сервер
   * берёт работу по номеру (labor_index), а порядок строк формы может
   * расходиться с сервером (коллега сохранил свои работы перед нашими, а
   * опрос ещё не перечитал WO). null — работы на WO больше нет; ошибка —
   * запрос не отправляется (окно покажет её текст).
   */
  const serverLaborIndex = async (laborId: string): Promise<number | null> => {
    if (!id) return null;
    try {
      const fresh = await fetchMechanicWoDetails(id, { mergeProtocol: true, timeoutMs: REFRESH_TIMEOUT_MS });
      const i = (fresh.labors || []).findIndex((l) => l.labor_id === laborId);
      return i >= 0 ? i : null;
    } catch (e) {
      if (!(e instanceof ApiError && LOCK_ERROR_CODES.has(e.code))) throw e;
    }
    // WO закрыт для механика (менеджер подтвердил): детали не отдаются, а
    // офис строки двигать может. Номер — по порядку строк, который форма
    // знает, и только если ревизия на сервере та же: подтверждение её не
    // двигает, любая запись строк — двигает. Ушла — порядок мог поменяться,
    // запрос ушёл бы о чужой работе: не отправляем.
    let rev: number;
    try {
      rev = Number((await fetchWorkOrderRev(id)).labors_rev);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) throw e;
      if (e.code === "work_order_not_found") return null;
      // Старый сервер (эндпоинта ревизии нет, строки он заменяет целиком) —
      // как раньше: номер по сохранённым строкам формы.
      const i = laborsRef.current.filter((l) => !!l.labor_id).findIndex((l) => l.labor_id === laborId);
      return i >= 0 ? i : null;
    }
    const order = serverOrderRef.current;
    if (!order || !Number.isFinite(rev) || rev !== revRef.current) {
      throw new ApiError(LOCKED_CHANGED_AUTH_TEXT, 409, "wo_changed_locked");
    }
    const i = order.indexOf(laborId);
    return i >= 0 ? i : null;
  };
  const authLaborId = authModal?.scope === "labor" ? authModal.laborId || "" : "";
  // Подпись — по текущей строке (её могли переименовать, пока окно открыто).
  const authJobLabel = authLaborId
    ? (labors.find((l) => l.labor_id === authLaborId)?.description || "").trim() || authModal?.jobLabel
    : authModal?.jobLabel;

  if (loading) {
    return (
      <View style={[styles.center, { backgroundColor: theme.bg }]}>
        <Stack.Screen options={{ title: "Work Order" }} />
        <ActivityIndicator color={theme.primary} size="large" />
      </View>
    );
  }

  const pickerStyle = [
    styles.picker,
    { backgroundColor: theme.surface, borderColor: theme.border },
  ];

  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: theme.bg }}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <Stack.Screen
        options={{
          title: isMechanic && isEdit && woMeta?.wo_number
            ? `WO #${woMeta.wo_number}`
            : isEdit ? "Edit Work Order" : "New Work Order",
        }}
      />
      <ScrollView contentContainerStyle={{ padding: 12, paddingBottom: 48 }} keyboardShouldPersistTaps="handled">
        {isMechanic && isEdit && woMeta ? (
          <View style={styles.mechHeaderRow}>
            <Text style={{ color: theme.text, fontWeight: "800", fontSize: 16 }}>
              WO #{woMeta.wo_number ?? ""}
            </Text>
            <View style={{ flexDirection: "row", gap: 6 }}>
              {/* Механик закончил — статус для него «Done», а не «In Progress». */}
              {woMeta.status === "paid" ? (
                <Badge label="Paid" tone="success" />
              ) : woMeta.mechanic_done ? (
                <Badge label="Done" tone="success" />
              ) : woMeta.status === "in_progress" ? (
                <Badge label="In Progress" tone="info" />
              ) : (
                <Badge label="Open" tone="warning" />
              )}
            </View>
          </View>
        ) : null}
        {isMechanic && isPaid ? (
          <Text style={{ color: theme.warning, fontSize: 13, marginBottom: 8 }}>
            This work order is paid and locked — view only.
          </Text>
        ) : null}
        {/* Замок менеджера (подтверждён / смета): текст сервера, автосейв стоит */}
        {isMechanic && isEdit && !isPaid && lockMessage ? (
          <Text style={{ color: theme.warning, fontSize: 13, marginBottom: 8 }}>{lockMessage}</Text>
        ) : null}

        {/* Клиент и юнит */}
        <Text style={[styles.sectionTitle, { color: theme.muted }]}>CUSTOMER & UNIT</Text>
        {!isEdit ? (
          <Pressable
            style={[styles.scanVinBtn, { borderColor: theme.primary, backgroundColor: theme.surface }]}
            onPress={() => setVinScannerOpen(true)}
            disabled={vinBusy}
          >
            {vinBusy ? (
              <ActivityIndicator size="small" color={theme.primary} />
            ) : (
              <>
                <Ionicons name="barcode-outline" size={20} color={theme.primary} />
                <Text style={{ color: theme.primary, fontWeight: "700", fontSize: 15 }}>
                  Scan VIN
                </Text>
              </>
            )}
          </Pressable>
        ) : null}
        <Pressable
          style={pickerStyle}
          onPress={() => !isEdit && setCustomerModal(true)}
          disabled={isEdit}
        >
          <Text style={{ color: customer ? theme.text : theme.muted, fontSize: 15 }}>
            {customer?.label || "Select customer…"}
          </Text>
          {!isEdit ? <Ionicons name="chevron-down" size={16} color={theme.muted} /> : null}
        </Pressable>
        <Pressable
          style={pickerStyle}
          onPress={() => !isEdit && customer && setUnitModal(true)}
          disabled={isEdit || !customer}
        >
          <Text style={{ color: unit ? theme.text : theme.muted, fontSize: 15 }}>
            {unit?.label || (customer ? "Select unit…" : "Select customer first")}
          </Text>
          {!isEdit ? <Ionicons name="chevron-down" size={16} color={theme.muted} /> : null}
        </Pressable>
        {isMechanic ? (
          <Field
            label="Unit mileage"
            value={mileage}
            onChangeText={editMileage}
            keyboardType="number-pad"
            placeholder="e.g. 152300"
          />
        ) : null}

        {/* Записи календаря, привязанные к WO — как в деталке WO у офиса */}
        {isMechanic && isEdit && appointments.length ? (
          <>
            <Text style={[styles.sectionTitle, { color: theme.muted }]}>SCHEDULED</Text>
            {appointments.map((a, idx) => (
              <RowCard key={a.id || String(idx)}>
                <View style={styles.apptHeader}>
                  <Text style={[styles.apptTitle, { color: theme.text }]} numberOfLines={1}>
                    {appointmentWhen(a)}
                  </Text>
                  {a.status ? (
                    <Badge label={appointmentStatusLabel(a.status)} tone={APPOINTMENT_TONES[a.status] || "muted"} />
                  ) : null}
                </View>
                <KV label="Mechanics" value={(a.mechanics || []).map((m) => m.name).filter(Boolean).join(", ")} />
                <KV label="Presets" value={(a.presets || []).map((p) => p.name).filter(Boolean).join(", ")} />
                {a.note ? <Text style={[styles.apptNote, { color: theme.muted }]}>Note: {a.note}</Text> : null}
              </RowCard>
            ))}
          </>
        ) : null}

        {/* Лейборы */}
        <Text style={[styles.sectionTitle, { color: theme.muted }]}>LABORS</Text>
        {/* Последнее сохранение слилось с чужими правками — что именно сервер сделал */}
        {mergeNotes.length ? (
          <RowCard style={{ borderColor: theme.primary }}>
            <View style={styles.noticeHeader}>
              <Ionicons name="information-circle-outline" size={18} color={theme.primary} />
              <Text style={[styles.noticeTitle, { color: theme.text }]}>Merged with changes from others</Text>
              <Pressable onPress={() => setMergeNotes([])} hitSlop={8} accessibilityLabel="Dismiss">
                <Ionicons name="close" size={18} color={theme.muted} />
              </Pressable>
            </View>
            {mergeNotes.map((note, i) => (
              <Text key={i} style={{ color: theme.muted, fontSize: 13 }}>
                • {note}
              </Text>
            ))}
          </RowCard>
        ) : null}
        {labors.map((labor, idx) => (
          <RowCard key={idx} style={{ borderWidth: 1.5, borderColor: theme.borderStrong }}>
            <View style={styles.laborHeader}>
              <Text style={{ color: theme.text, fontWeight: "700" }}>Labor {idx + 1}</Text>
              {labors.length > 1 ? (
                <Pressable onPress={() => removeLabor(idx)} hitSlop={8}>
                  <Ionicons name="trash-outline" size={18} color={theme.danger} />
                </Pressable>
              ) : null}
            </View>

            <Field
              label="Description"
              value={labor.description}
              onChangeText={(v) => patchLabor(idx, { description: v })}
              placeholder="Brake job…"
            />
            {!isMechanic ? (
              <View style={styles.hoursRow}>
                <View style={{ flex: 1 }}>
                  <Field
                    label="Hours"
                    value={labor.hours}
                    onChangeText={(v) => patchLabor(idx, { hours: v, labor_total: null })}
                    keyboardType="decimal-pad"
                    placeholder="0"
                  />
                </View>
                <View style={{ flex: 1 }}>
                  <Field
                    label="Labor total (manual)"
                    value={labor.labor_total ? String(labor.labor_total) : ""}
                    onChangeText={(v) => {
                      const num = parseFloat(v.replace(",", "."));
                      patchLabor(idx, { labor_total: Number.isFinite(num) ? num : null });
                    }}
                    keyboardType="decimal-pad"
                    placeholder="auto"
                  />
                </View>
              </View>
            ) : null}

            {/* Менеджер: свёрнутый тоггл на месте. Механик пишет описание
                кнопкой Describe issue рядом с Send for approval (ниже). */}
            {!isMechanic ? (
              <>
                <View style={styles.issueHeader}>
                  <Pressable
                    onPress={() => setIssueOpen((o) => ({ ...o, [idx]: !o[idx] }))}
                    hitSlop={8}
                    style={styles.issueToggle}
                  >
                    <Ionicons
                      name={issueOpen[idx] ? "chevron-down" : "chevron-forward"}
                      size={14}
                      color={theme.muted}
                    />
                    <Text style={[styles.fieldLabel, { color: theme.muted, marginTop: 0, marginBottom: 0 }]}>
                      ISSUE DESCRIPTION
                      {(labor.issue_description || "").trim() && !issueOpen[idx] ? " •" : ""}
                    </Text>
                  </Pressable>
                  {issueOpen[idx] ? (
                    <Pressable
                      onPress={() => polishIssue(idx)}
                      disabled={polishingIdx === idx}
                      hitSlop={8}
                      style={styles.aiBtn}
                    >
                      {polishingIdx === idx ? (
                        <ActivityIndicator size="small" color={theme.primary} />
                      ) : (
                        <>
                          <Ionicons name="sparkles-outline" size={14} color={theme.primary} />
                          <Text style={{ color: theme.primary, fontSize: 12, fontWeight: "700" }}>
                            AI edit
                          </Text>
                        </>
                      )}
                    </Pressable>
                  ) : null}
                </View>
                {issueOpen[idx] ? (
                  <TextInput
                    style={[
                      styles.issueInput,
                      { backgroundColor: theme.surface, borderColor: theme.border, color: theme.text },
                    ]}
                    value={labor.issue_description || ""}
                    onChangeText={(v) => patchLabor(idx, { issue_description: v })}
                    placeholder="Customer-reported issue…"
                    placeholderTextColor={theme.muted}
                    multiline
                  />
                ) : null}
              </>
            ) : null}

            {!isMechanic ? (
              <>
                <Text style={[styles.fieldLabel, { color: theme.muted }]}>RATE</Text>
                <View style={styles.rateRow}>
                  {rates.map((r) => {
                    const active = labor.rate_code === r.code;
                    return (
                      <Pressable
                        key={r.code}
                        onPress={() => patchLabor(idx, { rate_code: r.code })}
                        style={[
                          styles.rateChip,
                          {
                            backgroundColor: active ? theme.primary : theme.surfaceSoft,
                            borderColor: active ? theme.primary : theme.border,
                          },
                        ]}
                      >
                        <Text style={{ color: active ? "#fff" : theme.text, fontSize: 12, fontWeight: "600" }}>
                          {r.name} (${r.hourly_rate ?? 0}/h)
                        </Text>
                      </Pressable>
                    );
                  })}
                </View>
              </>
            ) : null}

            {/* Запчасти лейбора */}
            {labor.parts.map((p, pIdx) => (
              <View key={pIdx} style={[styles.partRow, { borderColor: theme.border }]}>
                <View style={{ flex: 1 }}>
                  <Text style={{ color: theme.text, fontSize: 13, fontWeight: "600" }} numberOfLines={1}>
                    {p.part_number || p.description || "Part"}
                  </Text>
                  {!isMechanic ? (
                    <Text style={{ color: theme.muted, fontSize: 12 }}>
                      {money(p.price ?? 0)} × {p.qty} = {money((p.price ?? 0) * p.qty)}
                    </Text>
                  ) : p.description && p.part_number ? (
                    <Text style={{ color: theme.muted, fontSize: 12 }} numberOfLines={1}>
                      {p.description}
                    </Text>
                  ) : null}
                </View>
                <TextInput
                  style={[styles.qtyInput, { borderColor: theme.border, color: theme.text }]}
                  value={String(p.qty || "")}
                  onChangeText={(v) => patchPart(idx, pIdx, { qty: parseInt(v, 10) || 0 })}
                  keyboardType="number-pad"
                />
                {!isMechanic ? (
                  <TextInput
                    style={[styles.priceInput, { borderColor: theme.border, color: theme.text }]}
                    value={String(p.price || "")}
                    onChangeText={(v) => {
                      const num = parseFloat(v.replace(",", "."));
                      patchPart(idx, pIdx, { price: Number.isFinite(num) ? num : 0 });
                    }}
                    keyboardType="decimal-pad"
                  />
                ) : null}
                <Pressable onPress={() => removePart(idx, pIdx)} hitSlop={8}>
                  <Ionicons name="close-circle" size={20} color={theme.danger} />
                </Pressable>
              </View>
            ))}

            <Pressable
              style={[styles.addPartBtn, { borderColor: theme.primary }]}
              onPress={() => setPartTarget(laborRefAt(labors, idx))}
            >
              <Ionicons name="add-circle-outline" size={20} color={theme.primary} />
              <Text style={{ color: theme.primary, fontWeight: "700", fontSize: 15 }}>Add part</Text>
            </Pressable>

            {isMechanic && isEdit && id && labor.labor_id ? (
              <>
                {!isPaid ? (
                  <LaborTimer
                    woId={id}
                    laborId={labor.labor_id}
                    completedSeconds={laborTimes[labor.labor_id]?.completed_seconds || 0}
                    runningUsers={laborTimes[labor.labor_id]?.running_users || []}
                    myRunning={!!laborTimes[labor.labor_id]?.my_running}
                    serverOffsetMs={serverOffsetMs}
                    onChanged={onTimerChanged}
                  />
                ) : null}
                <AttachmentsBlock
                  entityType="work_order_labor"
                  entityId={id}
                  parentId={labor.labor_id}
                  title="Job photos"
                />
                {!isPaid ? (
                  <View style={{ flexDirection: "row", gap: 8 }}>
                    <Pressable
                      style={[styles.jobApprovalBtn, { borderColor: theme.border, flex: 1 }]}
                      onPress={() => setIssueOpen((o) => ({ ...o, [idx]: !o[idx] }))}
                    >
                      <Ionicons name="create-outline" size={14} color={theme.primary} />
                      <Text style={{ color: theme.primary, fontWeight: "600", fontSize: 13 }}>
                        Describe issue
                        {(labor.issue_description || "").trim() ? " •" : ""}
                      </Text>
                    </Pressable>
                    <Pressable
                      style={[styles.jobApprovalBtn, { borderColor: theme.border, flex: 1 }]}
                      onPress={() =>
                        setAuthModal({
                          scope: "labor",
                          laborId: labor.labor_id || "",
                          jobLabel: labor.description || `Job ${idx + 1}`,
                        })
                      }
                    >
                      <Ionicons name="send-outline" size={14} color={theme.primary} />
                      <Text style={{ color: theme.primary, fontWeight: "600", fontSize: 13 }}>
                        Send for approval
                      </Text>
                    </Pressable>
                  </View>
                ) : null}
                {issueOpen[idx] ? (
                  <>
                    <View style={styles.issueHeader}>
                      <Text style={[styles.fieldLabel, { color: theme.muted, marginTop: 0, marginBottom: 0 }]}>
                        ISSUE DESCRIPTION
                      </Text>
                      <Pressable
                        onPress={() => polishIssue(idx)}
                        disabled={polishingIdx === idx}
                        hitSlop={8}
                        style={styles.aiBtn}
                      >
                        {polishingIdx === idx ? (
                          <ActivityIndicator size="small" color={theme.primary} />
                        ) : (
                          <>
                            <Ionicons name="sparkles-outline" size={14} color={theme.primary} />
                            <Text style={{ color: theme.primary, fontSize: 12, fontWeight: "700" }}>
                              AI edit
                            </Text>
                          </>
                        )}
                      </Pressable>
                    </View>
                    <TextInput
                      style={[
                        styles.issueInput,
                        { backgroundColor: theme.surface, borderColor: theme.border, color: theme.text },
                      ]}
                      value={labor.issue_description || ""}
                      onChangeText={(v) => patchLabor(idx, { issue_description: v })}
                      placeholder="Customer-reported issue…"
                      placeholderTextColor={theme.muted}
                      multiline
                      editable={!isPaid}
                    />
                  </>
                ) : null}
              </>
            ) : null}
          </RowCard>
        ))}

        <View style={styles.addRow}>
          <Pressable
            style={[styles.addLaborBtn, { borderColor: theme.border, backgroundColor: theme.surface, flex: 1 }]}
            onPress={() => editLabors((prev) => [...prev, emptyLabor()])}
          >
            <Ionicons name="add" size={18} color={theme.primary} />
            <Text style={{ color: theme.primary, fontWeight: "700" }}>Add labor</Text>
          </Pressable>
          <Pressable
            style={[styles.addLaborBtn, { borderColor: theme.border, backgroundColor: theme.surface, flex: 1 }]}
            onPress={() => setPresetModal(true)}
          >
            <Ionicons name="flash-outline" size={18} color={theme.primary} />
            <Text style={{ color: theme.primary, fontWeight: "700" }}>From preset</Text>
          </Pressable>
          {!isMechanic ? (
            <Pressable
              style={[styles.addLaborBtn, { borderColor: theme.border, backgroundColor: theme.surface, flex: 1 }]}
              onPress={scanPaperWo}
              disabled={scanning}
            >
              {scanning ? (
                <ActivityIndicator size="small" color={theme.primary} />
              ) : (
                <>
                  <Ionicons name="camera-outline" size={18} color={theme.primary} />
                  <Text style={{ color: theme.primary, fontWeight: "700" }}>AI scan</Text>
                </>
              )}
            </Pressable>
          ) : null}
        </View>

        {/* Итого (механику не показываем) */}
        {!isMechanic ? (
          <RowCard>
            <View style={styles.estimateRow}>
              <Text style={{ color: theme.muted, fontSize: 13 }}>Labor</Text>
              <Text style={{ color: theme.text, fontWeight: "600" }}>{money(estimate.laborSum)}</Text>
            </View>
            <View style={styles.estimateRow}>
              <Text style={{ color: theme.muted, fontSize: 13 }}>Parts</Text>
              <Text style={{ color: theme.text, fontWeight: "600" }}>{money(estimate.partsSum)}</Text>
            </View>
            <View style={styles.estimateRow}>
              <Text style={{ color: theme.text, fontWeight: "800", fontSize: 15 }}>Subtotal</Text>
              <Text style={{ color: theme.text, fontWeight: "800", fontSize: 15 }}>
                {money(estimate.total)}
              </Text>
            </View>
            <Text style={{ color: theme.muted, fontSize: 11 }}>
              Shop supply & tax are applied on save.
            </Text>
          </RowCard>
        ) : null}

        {isMechanic ? (
          <>
            {isEdit && id ? (
              <>
                <AttachmentsBlock entityType="work_order" entityId={id} />
                {!isPaid ? (
                  <Pressable
                    style={[styles.jobApprovalBtn, { borderColor: theme.border, marginTop: 14 }]}
                    onPress={() => setAuthModal({ scope: "work_order" })}
                  >
                    <Ionicons name="send-outline" size={15} color={theme.primary} />
                    <Text style={{ color: theme.primary, fontWeight: "600" }}>
                      Send work order for approval
                    </Text>
                  </Pressable>
                ) : null}
                {!isPaid ? (
                  <>
                    <Text
                      style={{
                        color: lockMessage || autoSaveState === "error" ? theme.warning : theme.muted,
                        fontSize: 11,
                        textAlign: "center",
                        marginTop: 10,
                      }}
                    >
                      {lockMessage
                        ? "Changes can't be saved while the work order is locked."
                        : autoSaveState === "saving"
                          ? "Saving…"
                          : autoSaveState === "error"
                            ? `Autosave failed — ${autoSaveError || "check connection."} It will retry automatically.`
                            : "Changes are saved automatically as In Progress."}
                    </Text>
                    <Pressable
                      style={[styles.doneBtn, { borderColor: theme.primary }]}
                      onPress={() => save("in_progress", "done")}
                      disabled={busy}
                    >
                      <Ionicons name="checkmark-done-outline" size={18} color={theme.primary} />
                      <Text style={{ color: theme.primary, fontWeight: "700" }}>Done — ready for review</Text>
                    </Pressable>
                    <Text style={{ color: theme.muted, fontSize: 11, textAlign: "center", marginTop: 6 }}>
                      "Done" tells the manager you finished — they still review and complete the WO.
                    </Text>
                  </>
                ) : null}
              </>
            ) : (
              <View style={{ marginTop: 16, alignItems: "center", gap: 8 }}>
                {busy ? <ActivityIndicator color={theme.primary} /> : null}
                <Text style={{ color: theme.muted, fontSize: 12, textAlign: "center" }}>
                  {busy
                    ? "Creating work order…"
                    : "Pick a customer and unit — the work order is created automatically."}
                </Text>
              </View>
            )}

            <MileageGateModal
              visible={isMechanic && mileageGate}
              unitLabel={unit?.label || ""}
              lastKnown={isEdit ? lastKnownMileage : (unit?.mileage != null && unit.mileage !== "" ? String(unit.mileage) : "")}
              onConfirm={(value) => {
                // Подтверждённый пробег — правка: уходит ближайшим автосейвом.
                editSeq.current += 1;
                flushAutosave.current = true;
                setMileage(value);
                setMileageGate(false);
              }}
              onLeave={() => router.back()}
            />
            {isEdit && id ? (
              <WoAuthorizationModal
                visible={!!authModal}
                onClose={() => setAuthModal(null)}
                woId={id}
                defaultEmail={woMeta?.customer_email || ""}
                scope={authModal?.scope || "work_order"}
                laborId={authLaborId || undefined}
                resolveLaborIndex={authLaborId ? () => serverLaborIndex(authLaborId) : undefined}
                jobLabel={authJobLabel}
              />
            ) : null}
          </>
        ) : (
          <>
            <SubmitButton title={isEdit ? "Save" : "Create work order"} onPress={() => save("open")} busy={busy} />
            <Pressable
              style={[styles.inProgressBtn, { borderColor: theme.border }]}
              onPress={() => save("in_progress")}
              disabled={busy}
            >
              <Text style={{ color: theme.text, fontWeight: "600" }}>Save as In Progress</Text>
            </Pressable>
          </>
        )}
      </ScrollView>

      {/* Модалки выбора */}
      <PickCustomerModal visible={customerModal} onClose={() => setCustomerModal(false)} onPick={pickCustomer} />
      <PickUnitModal
        visible={unitModal}
        units={units}
        onClose={() => setUnitModal(false)}
        onPick={(u) => {
          setUnit(u);
          setUnitModal(false);
        }}
        onCreateNew={
          customer
            ? () => {
                setUnitModal(false);
                router.push({ pathname: "/unit-form", params: { customerId: customer.id } });
              }
            : undefined
        }
      />
      <PickPartModal visible={partTarget !== null} onClose={() => setPartTarget(null)} onPick={addPart} />
      <PickPresetModal visible={presetModal} onClose={() => setPresetModal(false)} onPick={applyPreset} />
      <VinScannerModal
        visible={vinScannerOpen}
        onClose={() => setVinScannerOpen(false)}
        onVin={onVinScanned}
      />
      <VinCompanyChoiceModal
        matches={vinChoices}
        onClose={() => setVinChoices(null)}
        onPick={applyVinMatch}
      />
    </KeyboardAvoidingView>
  );
}

// VIN активен на нескольких компаниях — механик выбирает нужную.
function VinCompanyChoiceModal({
  matches,
  onClose,
  onPick,
}: {
  matches: VinMatch[] | null;
  onClose: () => void;
  onPick: (m: VinMatch) => void;
}) {
  const theme = useTheme();
  return (
    <Modal visible={!!matches} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.choiceBackdrop}>
        <View style={[styles.choiceSheet, { backgroundColor: theme.bg, borderColor: theme.border }]}>
          <View style={styles.modalHeader}>
            <Text style={{ color: theme.text, fontSize: 16, fontWeight: "800" }}>
              This unit is on several companies
            </Text>
            <Pressable onPress={onClose} hitSlop={8}>
              <Ionicons name="close" size={24} color={theme.muted} />
            </Pressable>
          </View>
          <Text style={{ color: theme.muted, fontSize: 13, paddingHorizontal: 16, marginBottom: 6 }}>
            Pick the company this work order belongs to:
          </Text>
          {(matches || []).map((m) => (
            <Pressable
              key={m.unit_id}
              style={[styles.modalItem, { borderBottomColor: theme.border }]}
              onPress={() => onPick(m)}
            >
              <Text style={{ color: theme.text, fontSize: 15, fontWeight: "700" }}>{m.customer_label}</Text>
              <Text style={{ color: theme.muted, fontSize: 13, marginTop: 2 }}>{m.unit_label}</Text>
            </Pressable>
          ))}
        </View>
      </View>
    </Modal>
  );
}

// ── модалки выбора ───────────────────────────────────────────────────

function PickCustomerModal({
  visible,
  onClose,
  onPick,
}: {
  visible: boolean;
  onClose: () => void;
  onPick: (c: CustomerRow) => void;
}) {
  // У механика нет customers.view — список клиентов берём из WO-эндпоинта
  // (work_orders.view), иначе пикер показывал бы 0 клиентов.
  const isMechanic = useIsMechanic();
  const search = (q: string) =>
    isMechanic ? fetchWoFormCustomers(q) : fetchCustomers(q, 1).then((d) => d.items);
  return (
    <SearchPickerModal<CustomerRow>
      visible={visible}
      onClose={onClose}
      title="Select customer"
      placeholder="Search customers…"
      search={search}
      renderLabel={(c) => c.company_name || c.contact_name || "—"}
      onPick={onPick}
    />
  );
}

function PickUnitModal({
  visible,
  units,
  onClose,
  onPick,
  onCreateNew,
}: {
  visible: boolean;
  units: UnitOption[];
  onClose: () => void;
  onPick: (u: UnitOption) => void;
  onCreateNew?: () => void;
}) {
  const theme = useTheme();
  // Поиск как в пикере клиента; юнитов у флита может быть много.
  const [q, setQ] = useState("");
  useEffect(() => {
    if (visible) setQ("");
  }, [visible]);
  const query = q.trim().toLowerCase();
  const filtered = query ? units.filter((u) => u.label.toLowerCase().includes(query)) : units;
  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: theme.bg, paddingTop: 56 }}>
        <View style={styles.modalHeader}>
          <Text style={{ color: theme.text, fontSize: 17, fontWeight: "800" }}>Select unit</Text>
          <Pressable onPress={onClose} hitSlop={8}>
            <Ionicons name="close" size={24} color={theme.muted} />
          </Pressable>
        </View>
        <TextInput
          style={[
            styles.modalSearch,
            { backgroundColor: theme.surface, borderColor: theme.border, color: theme.text },
          ]}
          value={q}
          onChangeText={setQ}
          placeholder="Search units…"
          placeholderTextColor={theme.muted}
          autoFocus
        />
        <FlatList
          data={filtered}
          keyExtractor={(u) => u.id}
          ListHeaderComponent={
            onCreateNew ? (
              <Pressable
                style={[styles.modalItem, { borderBottomColor: theme.border, flexDirection: "row", alignItems: "center", gap: 8 }]}
                onPress={onCreateNew}
              >
                <Ionicons name="add-circle-outline" size={20} color={theme.primary} />
                <Text style={{ color: theme.primary, fontSize: 15, fontWeight: "600" }}>New unit</Text>
              </Pressable>
            ) : null
          }
          renderItem={({ item }) => (
            <Pressable
              style={[styles.modalItem, { borderBottomColor: theme.border }]}
              onPress={() => onPick(item)}
            >
              <Text style={{ color: theme.text, fontSize: 15 }}>{item.label}</Text>
            </Pressable>
          )}
          keyboardShouldPersistTaps="handled"
          ListEmptyComponent={
            <Text style={{ color: theme.muted, textAlign: "center", marginTop: 32 }}>
              {query ? "No units match your search." : "This customer has no units yet."}
            </Text>
          }
        />
      </View>
    </Modal>
  );
}

function PickPresetModal({
  visible,
  onClose,
  onPick,
}: {
  visible: boolean;
  onClose: () => void;
  onPick: (p: PresetListItem) => void;
}) {
  return (
    <SearchPickerModal<PresetListItem>
      visible={visible}
      onClose={onClose}
      title="Service templates"
      placeholder="Filter presets…"
      search={(q) =>
        fetchPresets().then((items) =>
          q ? items.filter((p) => p.name.toLowerCase().includes(q.toLowerCase())) : items
        )
      }
      renderLabel={(p) => p.name}
      onPick={onPick}
    />
  );
}

// Синтетическая строка «one-time part»: парта нет в каталоге, номер — из
// строки поиска, цену заполнит менеджер.
type PartPickItem = PartSearchItem & { one_time?: boolean };

function PickPartModal({
  visible,
  onClose,
  onPick,
}: {
  visible: boolean;
  onClose: () => void;
  onPick: (p: PartPickItem) => void;
}) {
  const search = async (q: string): Promise<PartPickItem[]> => {
    if (q.trim().length < 2) return [];
    let items: PartPickItem[] = [];
    try {
      items = flattenPartAlternates(await searchParts(q));
    } catch {
      // Каталожный поиск упал — one-time строка всё равно должна остаться.
      items = [];
    }
    // Как на вебе: всегда можно добавить введённое как one-time part.
    items.unshift({
      id: "",
      part_number: q.trim(),
      description: "",
      reference: "",
      in_stock: 0,
      one_time: true,
    });
    return items;
  };
  return (
    <SearchPickerModal<PartPickItem>
      visible={visible}
      onClose={onClose}
      title="Add part"
      placeholder="Part number or description…"
      search={search}
      renderLabel={(p) =>
        p.one_time
          ? `＋ Add "${p.part_number}" as one-time part (not from catalog)`
          : `${p.alt_for ? `⇄ ` : ""}${p.part_number} — ${p.description || ""} (×${p.in_stock})` +
            (p.alt_for ? ` · fits ${p.alt_for}` : "")
      }
      onPick={onPick}
    />
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  sectionTitle: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: 14, marginBottom: 4 },
  picker: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 13,
    marginTop: 6,
  },
  laborHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  apptHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 },
  apptTitle: { fontSize: 14, fontWeight: "700", flex: 1 },
  apptNote: { fontSize: 12, fontStyle: "italic" },
  hoursRow: { flexDirection: "row", gap: 8 },
  fieldLabel: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: 10, marginBottom: 4 },
  rateRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  rateChip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 5 },
  partRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    borderTopWidth: 1,
    paddingTop: 8,
    marginTop: 8,
  },
  qtyInput: {
    borderWidth: 1,
    borderRadius: 8,
    width: 52,
    textAlign: "center",
    paddingVertical: 6,
    fontSize: 14,
  },
  priceInput: {
    borderWidth: 1,
    borderRadius: 8,
    width: 74,
    textAlign: "center",
    paddingVertical: 6,
    fontSize: 14,
  },
  addPartBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    borderWidth: 1.5,
    borderStyle: "dashed",
    borderRadius: 12,
    paddingVertical: 13,
    marginTop: 12,
  },
  addRow: { flexDirection: "row", gap: 8 },
  issueHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 10,
  },
  aiBtn: { flexDirection: "row", alignItems: "center", gap: 4 },
  issueToggle: { flexDirection: "row", alignItems: "center", gap: 4 },
  issueInput: {
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
    minHeight: 56,
    textAlignVertical: "top",
  },
  addLaborBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    borderWidth: 1,
    borderRadius: 12,
    paddingVertical: 12,
    marginTop: 8,
  },
  estimateRow: { flexDirection: "row", justifyContent: "space-between", marginTop: 2 },
  inProgressBtn: {
    borderWidth: 1,
    borderRadius: 12,
    paddingVertical: 13,
    alignItems: "center",
    marginTop: 10,
  },
  doneBtn: {
    flexDirection: "row",
    justifyContent: "center",
    gap: 8,
    borderWidth: 1,
    borderRadius: 12,
    paddingVertical: 13,
    alignItems: "center",
    marginTop: 10,
  },
  mechHeaderRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 8,
  },
  noticeHeader: { flexDirection: "row", alignItems: "center", gap: 6 },
  noticeTitle: { flex: 1, fontSize: 14, fontWeight: "700" },
  jobApprovalBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    borderWidth: 1,
    borderRadius: 10,
    paddingVertical: 9,
    marginTop: 10,
  },
  modalHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: 16,
    paddingBottom: 10,
  },
  modalSearch: {
    borderWidth: 1,
    borderRadius: 10,
    marginHorizontal: 16,
    marginBottom: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 15,
  },
  modalItem: { paddingHorizontal: 16, paddingVertical: 14, borderBottomWidth: 1 },
  scanVinBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    borderWidth: 1.5,
    borderStyle: "dashed",
    borderRadius: 12,
    paddingVertical: 13,
    marginTop: 6,
  },
  choiceBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.5)",
    justifyContent: "flex-end",
  },
  choiceSheet: {
    borderTopLeftRadius: 18,
    borderTopRightRadius: 18,
    borderWidth: 1,
    paddingTop: 14,
    paddingBottom: 34,
  },
});
