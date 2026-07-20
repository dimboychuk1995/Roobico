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

import { Field, SubmitButton } from "@/components/form";
import { SearchPickerModal } from "@/components/search-picker";
import { RowCard } from "@/components/ui";
import { useIsMechanic } from "@/context/auth";
import { useToast } from "@/context/toast";
import {
  ApiError,
  CustomerRow,
  LaborRate,
  PartSearchItem,
  PresetListItem,
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
  const { id, customerId: presetCustomerId } = useLocalSearchParams<{
    id?: string;
    customerId?: string;
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
  const [polishingIdx, setPolishingIdx] = useState<number | null>(null);
  const [scanning, setScanning] = useState(false);

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
          setUnits(c.units.map((u) => ({ id: u.id, label: u.label })));
        }
      } catch (e) {
        toast.show(e instanceof ApiError ? e.message : "Failed to load.", "error");
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pickCustomer = async (c: CustomerRow) => {
    setCustomer({ id: c.id, label: c.company_name || c.contact_name || "—" });
    setUnit(null);
    setCustomerModal(false);
    try {
      const details = await fetchCustomerDetails(c.id);
      setUnits(details.units.map((u) => ({ id: u.id, label: u.label })));
    } catch {
      setUnits([]);
    }
  };

  // Возврат с экрана создания юнита: обновить список юнитов клиента и,
  // если появился ровно один новый, выбрать его автоматически.
  const refreshUnitsOnFocus = useCallback(() => {
    if (isEdit || !customer) return;
    fetchCustomerDetails(customer.id)
      .then((details) => {
        const fresh = details.units.map((u) => ({ id: u.id, label: u.label }));
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
  }, [customer?.id, isEdit, unit]);

  useFocusEffect(refreshUnitsOnFocus);

  // ── лейборы ───────────────────────────────────────────────────────
  const patchLabor = (idx: number, patch: Partial<WoFormLabor>) => {
    setLabors((prev) => prev.map((l, i) => (i === idx ? { ...l, ...patch } : l)));
  };

  const removeLabor = (idx: number) => {
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
    setLabors((prev) =>
      prev.map((l, i) =>
        i === laborIdx ? { ...l, parts: l.parts.filter((_, j) => j !== partIdx) } : l
      )
    );
  };

  const addPart = async (item: PartSearchItem) => {
    const laborIdx = partModalLabor;
    setPartModalLabor(null);
    if (laborIdx === null) return;

    let newPart: WoFormPart;
    if (isMechanic) {
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
        parts,
      };
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

  // ── сохранение ────────────────────────────────────────────────────
  const save = async (status: "open" | "in_progress") => {
    if (!isEdit && (!customer || !unit)) {
      toast.show("Select customer and unit.", "error");
      return;
    }
    let validLabors = labors.filter(
      (l) => l.description.trim() || l.parts.length > 0
    );
    if (!validLabors.length) {
      toast.show("Add at least one labor with a description.", "error");
      return;
    }
    if (isMechanic) {
      validLabors = stripMoney(validLabors);
      status = "in_progress";
    }

    setBusy(true);
    try {
      let res;
      if (isEdit && id) {
        res = await editWorkOrder(id, { status, labors: validLabors });
      } else {
        res = await createWorkOrder({
          customer_id: customer!.id,
          unit_id: unit!.id,
          status,
          labors: validLabors,
        });
      }
      (res.inventory_warnings || []).forEach((w) => toast.show(w, "info"));
      toast.show(
        isEdit ? "Work order saved." : `Work order #${res.wo_number} created.`,
        "success"
      );
      router.back();
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Save failed.", "error");
    } finally {
      setBusy(false);
    }
  };

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
      <Stack.Screen options={{ title: isEdit ? "Edit Work Order" : "New Work Order" }} />
      <ScrollView contentContainerStyle={{ padding: 12, paddingBottom: 48 }} keyboardShouldPersistTaps="handled">
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

        {/* Лейборы */}
        <Text style={[styles.sectionTitle, { color: theme.muted }]}>LABORS</Text>
        {labors.map((labor, idx) => (
          <RowCard key={idx}>
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
            />

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

            <Pressable style={styles.addPartBtn} onPress={() => setPartModalLabor(idx)}>
              <Ionicons name="add-circle-outline" size={18} color={theme.primary} />
              <Text style={{ color: theme.primary, fontWeight: "600", fontSize: 13 }}>Add part</Text>
            </Pressable>
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
            <SubmitButton
              title={isEdit ? "Save" : "Create work order"}
              onPress={() => save("in_progress")}
              busy={busy}
            />
            <Text style={{ color: theme.muted, fontSize: 11, textAlign: "center", marginTop: 6 }}>
              Saved work orders go to the manager for review.
            </Text>
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
  return (
    <SearchPickerModal<CustomerRow>
      visible={visible}
      onClose={onClose}
      title="Select customer"
      placeholder="Search customers…"
      search={(q) => fetchCustomers(q, 1).then((d) => d.items)}
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
  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: theme.bg, paddingTop: 56 }}>
        <View style={styles.modalHeader}>
          <Text style={{ color: theme.text, fontSize: 17, fontWeight: "800" }}>Select unit</Text>
          <Pressable onPress={onClose} hitSlop={8}>
            <Ionicons name="close" size={24} color={theme.muted} />
          </Pressable>
        </View>
        <FlatList
          data={units}
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
          ListEmptyComponent={
            <Text style={{ color: theme.muted, textAlign: "center", marginTop: 32 }}>
              This customer has no units yet.
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

function PickPartModal({
  visible,
  onClose,
  onPick,
}: {
  visible: boolean;
  onClose: () => void;
  onPick: (p: PartSearchItem) => void;
}) {
  return (
    <SearchPickerModal<PartSearchItem>
      visible={visible}
      onClose={onClose}
      title="Add part"
      placeholder="Part number or description…"
      search={(q) =>
        q.length >= 2 ? searchParts(q).then(flattenPartAlternates) : Promise.resolve([])
      }
      renderLabel={(p) =>
        `${p.alt_for ? `⇄ ` : ""}${p.part_number} — ${p.description || ""} (×${p.in_stock})` +
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
  addPartBtn: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 10 },
  addRow: { flexDirection: "row", gap: 8 },
  issueHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 10,
  },
  aiBtn: { flexDirection: "row", alignItems: "center", gap: 4 },
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
