import Ionicons from "@expo/vector-icons/Ionicons";
import { Stack, useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
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
import { Field, SubmitButton } from "@/components/form";
import { LaborTimer } from "@/components/labor-timer";
import { SearchPickerModal } from "@/components/search-picker";
import { Badge, RowCard } from "@/components/ui";
import { WoAuthorizationModal } from "@/components/wo-authorization-modal";
import { useIsMechanic } from "@/context/auth";
import { useToast } from "@/context/toast";
import {
  ApiError,
  CustomerRow,
  LaborRate,
  MechLaborTime,
  PartSearchItem,
  PresetListItem,
  TimerResponse,
  WoFormLabor,
  WoFormPart,
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
  flattenPartAlternates,
  money,
  parseHandwrittenWo,
  polishIssueText,
  searchParts,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

interface UnitOption {
  id: string;
  label: string;
}

function emptyLabor(): WoFormLabor {
  return { description: "", hours: "", rate_code: "", labor_total: null, parts: [] };
}

export default function WorkOrderFormScreen() {
  const { id, customerId: presetCustomerId, unitId: presetUnitId } = useLocalSearchParams<{
    id?: string;
    customerId?: string;
    unitId?: string;
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
  const [partModalLabor, setPartModalLabor] = useState<number | null>(null);
  const [presetModal, setPresetModal] = useState(false);
  // Issue description свёрнуто по умолчанию — раскрывается по кнопке.
  const [issueOpen, setIssueOpen] = useState<Record<number, boolean>>({});
  const [polishingIdx, setPolishingIdx] = useState<number | null>(null);
  const [scanning, setScanning] = useState(false);

  // ── механик: всегда-edit вид (таймеры, фото, утверждение, автосейв) ──
  const [woMeta, setWoMeta] = useState<{
    wo_number: number | string | null;
    status: string;
    mechanic_done: boolean;
    customer_email: string;
  } | null>(null);
  const [mileage, setMileage] = useState("");
  const [laborTimes, setLaborTimes] = useState<Record<string, MechLaborTime>>({});
  const [serverOffsetMs, setServerOffsetMs] = useState(0);
  const [authModal, setAuthModal] = useState<{ scope: "work_order" | "labor"; laborIndex?: number; jobLabel?: string } | null>(null);
  const [autoSaveState, setAutoSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const loadedRef = useRef(false);
  const autoCreateFired = useRef(false);
  const editRevision = useRef(0);
  const savedRevision = useRef(0);
  const autosaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // setLabors из серверного рефетча не должен запускать новый автосейв.
  const suppressAutosave = useRef(false);
  // Структурные изменения (пресет, парты) сохраняем сразу, без debounce.
  const flushAutosave = useRef(false);

  const isPaid = woMeta?.status === "paid";

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
      setLabors((prev) => {
        const only = prev.length === 1 && !prev[0].description && prev[0].parts.length === 0;
        return only ? newLabors : [...prev, ...newLabors];
      });
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

  // ── загрузка ──────────────────────────────────────────────────────
  useEffect(() => {
    (async () => {
      try {
        const rateList = await fetchLaborRates();
        setRates(rateList);

        if (isEdit && id && isMechanic) {
          // Механик получает безденежную форму деталей; сервер при сохранении
          // склеит по labor_id и сохранит менеджерские часы/ставки/цены.
          const wo = await fetchMechanicWoDetails(id);
          setCustomer({ id: wo.customer.id, label: wo.customer.label });
          setUnit({ id: wo.unit.id, label: wo.unit.label });
          setWoMeta({
            wo_number: wo.wo_number,
            status: wo.status,
            mechanic_done: !!wo.mechanic_done,
            customer_email: wo.customer_email || "",
          });
          setMileage(wo.unit.mileage != null && wo.unit.mileage !== "" ? String(wo.unit.mileage) : "");
          const times: Record<string, MechLaborTime> = {};
          wo.labors.forEach((l) => {
            if (l.labor_id) times[l.labor_id] = l.time;
          });
          setLaborTimes(times);
          const t = Date.parse(wo.server_now);
          if (Number.isFinite(t)) setServerOffsetMs(t - Date.now());
          if (wo.labors.length) {
            setLabors(
              wo.labors.map((l) => ({
                labor_id: l.labor_id,
                description: l.description,
                hours: "",
                rate_code: "",
                labor_total: null,
                issue_description: l.issue_description,
                parts: l.parts.map((p) => ({
                  part_id: p.part_id || "",
                  part_number: p.part_number || "",
                  description: p.description || "",
                  qty: p.qty || 0,
                  one_time_part: !!p.one_time_part,
                })),
              }))
            );
          }
        } else if (isEdit && id) {
          const wo = await fetchWorkOrderDetails(id);
          setCustomer({ id: wo.customer_id, label: wo.cust_name });
          setUnit({ id: wo.unit_id, label: wo.unit_label });
          const raw = (wo as any).raw_labors as any[] | undefined;
          if (raw && raw.length) {
            setLabors(
              raw.map((l) => ({
                labor_id: l.labor_id || "",
                description: l.description,
                hours: l.hours,
                rate_code: l.rate_code,
                // Сохранённая база лейбора — чтобы правка не пересчитала
                // ручные суммы, введённые в вебе.
                labor_total: l.labor_base || null,
                issue_description: l.issue_description,
                parts: (l.parts || []).map((p: any) => ({
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
              }))
            );
          }
        } else if (presetCustomerId) {
          const c = await fetchCustomerDetails(presetCustomerId);
          setCustomer({ id: c.id, label: c.label });
          const opts = c.units.map((u) => ({ id: u.id, label: u.label }));
          setUnits(opts);
          // Переход из общего поиска юнитов: юнит уже выбран.
          if (presetUnitId) {
            const preset = opts.find((u) => u.id === presetUnitId);
            if (preset) setUnit(preset);
          }
        }
      } catch (e) {
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
            d.units.map((u) => ({ id: u.id, label: u.label }))
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
  const patchLabor = (idx: number, patch: Partial<WoFormLabor>) => {
    setLabors((prev) => prev.map((l, i) => (i === idx ? { ...l, ...patch } : l)));
  };

  const removeLabor = (idx: number) => {
    flushAutosave.current = true;
    setLabors((prev) => (prev.length > 1 ? prev.filter((_, i) => i !== idx) : prev));
  };

  const patchPart = (laborIdx: number, partIdx: number, patch: Partial<WoFormPart>) => {
    setLabors((prev) =>
      prev.map((l, i) =>
        i === laborIdx
          ? { ...l, parts: l.parts.map((p, j) => (j === partIdx ? { ...p, ...patch } : p)) }
          : l
      )
    );
  };

  const removePart = (laborIdx: number, partIdx: number) => {
    flushAutosave.current = true;
    setLabors((prev) =>
      prev.map((l, i) =>
        i === laborIdx ? { ...l, parts: l.parts.filter((_, j) => j !== partIdx) } : l
      )
    );
  };

  const addPart = async (item: PartSearchItem & { one_time?: boolean }) => {
    const laborIdx = partModalLabor;
    setPartModalLabor(null);
    if (laborIdx === null) return;

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
    flushAutosave.current = true;
    setLabors((prev) =>
      prev.map((l, i) => (i === laborIdx ? { ...l, parts: [...l.parts, newPart] } : l))
    );
  };

  // ── пресеты ───────────────────────────────────────────────────────
  const applyPreset = async (item: PresetListItem) => {
    setPresetModal(false);
    try {
      const preset = await fetchPresetDetail(item.id);
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
            const priced = await fetchPartPrice(p.part_id, customer?.id || "");
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
      const newLabor: WoFormLabor = {
        description: preset.name || preset.description || "",
        hours: isMechanic ? "" : preset.labor_hours != null ? String(preset.labor_hours) : "",
        rate_code: isMechanic ? "" : preset.labor_rate_code || "",
        labor_total: null,
        // Сервер по preset_id берёт часы/ставку пресета для работы механика.
        preset_id: item.id,
        parts,
      };
      flushAutosave.current = true;
      setLabors((prev) => {
        // Пустой единственный лейбор заменяем, иначе добавляем новый.
        const only = prev.length === 1 && !prev[0].description && prev[0].parts.length === 0;
        return only ? [newLabor] : [...prev, newLabor];
      });
      toast.show(`Preset "${preset.name}" applied.`, "success");
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Failed to load preset.", "error");
    }
  };

  // ── AI-полировка описания проблемы ────────────────────────────────
  const polishIssue = async (idx: number) => {
    const text = (labors[idx]?.issue_description || "").trim();
    if (!text) {
      toast.show("Write the issue description first.", "error");
      return;
    }
    setPolishingIdx(idx);
    try {
      const res = await polishIssueText(text);
      patchLabor(idx, { issue_description: res.polished });
      toast.show("Description polished by AI.", "success");
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "AI polish failed.", "error");
    } finally {
      setPolishingIdx(null);
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

  // Defense-in-depth: механик не должен передавать деньги даже нулями —
  // сервер их и так игнорирует, но и в трафике их быть не должно.
  const stripMoney = (items: WoFormLabor[]): WoFormLabor[] =>
    items.map(({ labor_total, ...l }) => ({
      ...l,
      hours: "",
      rate_code: "",
      parts: l.parts.map(({ cost, price, core_charge, misc_charge, ...p }) => p),
    }));

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
    // Старт таймера на сервере форсит in_progress и сбрасывает done.
    setWoMeta((m) => (m ? { ...m, status: "in_progress", mechanic_done: false } : m));
  }, []);

  // ── автосейв (механик, edit-вид): любое изменение → in_progress ───
  const doAutosave = useCallback(async () => {
    if (!isMechanic || !isEdit || !id || isPaid) return;
    const rev = editRevision.current;
    const validLabors = stripMoney(
      labors.filter((l) => l.description.trim() || l.parts.length > 0)
    );
    if (!validLabors.length) return;

    setAutoSaveState("saving");
    try {
      await editWorkOrder(id, {
        status: "in_progress",
        labors: validLabors,
        mechanic_state: "in_progress",
        unit_mileage: mileage.trim() || undefined,
      });
      savedRevision.current = rev;
      setAutoSaveState("saved");
      setWoMeta((m) => (m ? { ...m, status: "in_progress", mechanic_done: false } : m));

      // Новые СОХРАНЁННЫЕ работы получили labor_id на сервере — подтягиваем
      // его, чтобы появились таймер и фото. Пустая строка labor_id не имеет,
      // но и не сохранялась — рефетч из-за неё не нужен (иначе она пропадёт).
      const needsIds = labors.some(
        (l) => !l.labor_id && (l.description.trim() || l.parts.length > 0)
      );
      if (needsIds && editRevision.current === rev) {
        const fresh = await fetchMechanicWoDetails(id);
        if (editRevision.current === rev) {
          suppressAutosave.current = true;
          setLabors((prev) => {
            const mapped = fresh.labors.map((l) => ({
              labor_id: l.labor_id,
              description: l.description,
              hours: "",
              rate_code: "",
              labor_total: null,
              issue_description: l.issue_description,
              parts: l.parts.map((p) => ({
                part_id: p.part_id || "",
                part_number: p.part_number || "",
                description: p.description || "",
                qty: p.qty || 0,
                one_time_part: !!p.one_time_part,
              })),
            }));
            // Пустые несохранённые строки (только что добавленные) не теряем.
            const unsavedEmpty = prev.filter(
              (l) => !l.labor_id && !l.description.trim() && l.parts.length === 0
            );
            return unsavedEmpty.length ? [...mapped, ...unsavedEmpty] : mapped;
          });
          const times: Record<string, MechLaborTime> = {};
          fresh.labors.forEach((l) => {
            if (l.labor_id) times[l.labor_id] = l.time;
          });
          setLaborTimes(times);
        }
      }
    } catch {
      setAutoSaveState("error");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isMechanic, isEdit, id, isPaid, labors, mileage]);

  useEffect(() => {
    if (!isMechanic || !isEdit || !loadedRef.current || isPaid) return;
    // Обновление пришло с сервера (рефетч после автосейва) — не пересохраняем.
    if (suppressAutosave.current) {
      suppressAutosave.current = false;
      return;
    }
    editRevision.current += 1;
    if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    const delay = flushAutosave.current ? 50 : 1200;
    flushAutosave.current = false;
    autosaveTimer.current = setTimeout(() => {
      doAutosave();
    }, delay);
    return () => {
      if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [labors, mileage]);

  // ── сохранение ────────────────────────────────────────────────────
  // mechanicState: работа механика всегда сохраняется как in_progress —
  // "done" лишь помечает менеджеру, что механик закончил и ждёт проверки.
  const save = async (status: "open" | "in_progress", mechanicState?: "in_progress" | "done") => {
    if (!isEdit && (!customer || !unit)) {
      toast.show("Select customer and unit.", "error");
      return;
    }
    let validLabors = labors.filter(
      (l) => l.description.trim() || l.parts.length > 0
    );
    // Механик создаёт WO сразу при выборе клиента и юнита — работ ещё нет,
    // они добавятся автосейвом уже в edit-виде.
    if (!validLabors.length && !(isMechanic && !isEdit)) {
      toast.show("Add at least one labor with a description.", "error");
      return;
    }
    if (isMechanic) {
      validLabors = stripMoney(validLabors);
      status = "in_progress";
      // Явное сохранение отменяет запланированный автосейв.
      if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    }

    setBusy(true);
    try {
      let res;
      const mechanicFields = isMechanic
        ? { mechanic_state: mechanicState || "in_progress", unit_mileage: mileage.trim() || undefined }
        : {};
      if (isEdit && id) {
        res = await editWorkOrder(id, { status, labors: validLabors, ...mechanicFields });
      } else {
        res = await createWorkOrder({
          customer_id: customer!.id,
          unit_id: unit!.id,
          status,
          labors: validLabors,
          ...mechanicFields,
        });
      }
      (res.inventory_warnings || []).forEach((w) => toast.show(w, "info"));
      toast.show(
        isEdit ? "Work order saved." : `Work order #${res.wo_number} created.`,
        "success"
      );
      if (!isEdit && isMechanic && res.id) {
        // Механик после создания сразу попадает в рабочий edit-вид WO —
        // с таймерами, фото и отправкой на утверждение.
        router.replace({ pathname: "/work-order-form", params: { id: res.id } });
      } else {
        router.back();
      }
    } catch (e) {
      // Провал авто-создания: разрешаем повторный триггер по выбору юнита.
      autoCreateFired.current = false;
      toast.show(e instanceof ApiError ? e.message : "Save failed.", "error");
    } finally {
      setBusy(false);
    }
  };

  // Механик: WO создаётся сам, как только выбраны клиент и юнит.
  useEffect(() => {
    if (!isMechanic || isEdit || !customer || !unit || autoCreateFired.current) return;
    autoCreateFired.current = true;
    save("in_progress", "in_progress");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customer, unit]);

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

        {/* Клиент и юнит */}
        <Text style={[styles.sectionTitle, { color: theme.muted }]}>CUSTOMER & UNIT</Text>
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
            onChangeText={setMileage}
            keyboardType="number-pad"
            placeholder="e.g. 152300"
          />
        ) : null}

        {/* Лейборы */}
        <Text style={[styles.sectionTitle, { color: theme.muted }]}>LABORS</Text>
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
              onPress={() => setPartModalLabor(idx)}
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
                          laborIndex: idx,
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
            onPress={() => setLabors((prev) => [...prev, emptyLabor()])}
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
                    <Text style={{ color: theme.muted, fontSize: 11, textAlign: "center", marginTop: 10 }}>
                      {autoSaveState === "saving"
                        ? "Saving…"
                        : autoSaveState === "error"
                          ? "Autosave failed — check connection."
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

            {isEdit && id ? (
              <WoAuthorizationModal
                visible={!!authModal}
                onClose={() => setAuthModal(null)}
                woId={id}
                defaultEmail={woMeta?.customer_email || ""}
                scope={authModal?.scope || "work_order"}
                laborIndex={authModal?.laborIndex}
                jobLabel={authModal?.jobLabel}
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
      <PickPartModal visible={partModalLabor !== null} onClose={() => setPartModalLabor(null)} onPick={addPart} />
      <PickPresetModal visible={presetModal} onClose={() => setPresetModal(false)} onPick={applyPreset} />
    </KeyboardAvoidingView>
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
});
