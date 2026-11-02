/**
 * Модалки календаря: деталка события (запись или WO-блок) и форма
 * создания / редактирования записи с выбором времени чипами (шаг 15 мин).
 */
import Ionicons from "@expo/vector-icons/Ionicons";
import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
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

import { SearchPickerModal } from "@/components/search-picker";
import { useToast } from "@/context/toast";
import {
  ApiError,
  CalendarEvent,
  CalendarStatus,
  CustomerRow,
  createCalendarEvent,
  deleteCalendarEvent,
  fetchCalendarMechanics,
  fetchCustomerDetails,
  fetchCustomers,
  updateCalendarEvent,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

import {
  addDays,
  eventEnd,
  eventStart,
  eventTitle,
  fmtDayShort,
  fmtDuration,
  fmtMinutes,
  fmtTime,
  startOfDay,
  textOn,
} from "./dates";

// ── деталка ──────────────────────────────────────────────────────────

export function EventDetailModal({
  event,
  statuses,
  woStates,
  colorOf,
  canEdit,
  canDelete,
  canCreateWorkOrder,
  onClose,
  onChanged,
  onEdit,
  onOpenWorkOrder,
  onCreateWorkOrder,
}: {
  event: CalendarEvent | null;
  statuses: CalendarStatus[];
  woStates: CalendarStatus[];
  colorOf: (ev: CalendarEvent) => string;
  canEdit: boolean;
  canDelete: boolean;
  canCreateWorkOrder: boolean;
  onClose: () => void;
  onChanged: () => void;
  onEdit: (ev: CalendarEvent) => void;
  onOpenWorkOrder: (workOrderId: string) => void;
  onCreateWorkOrder: (ev: CalendarEvent) => void;
}) {
  const theme = useTheme();
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  if (!event) return null;
  const isWo = event.kind === "work_order";
  const start = eventStart(event);
  const end = eventEnd(event);
  const color = colorOf(event);
  const stateLabel = isWo || event.work_order_id
    ? woStates.find((s) => s.key === event.wo_state)?.label || event.wo_state
    : "";
  const worked = (event.worked || []).filter((w) => w.seconds > 0 || w.running);

  const setStatus = async (key: string) => {
    if (key === event.status || busy) return;
    setBusy(true);
    try {
      await updateCalendarEvent(event.id, { status: key });
      toast.show("Status updated.", "success");
      onChanged();
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Failed to update.", "error");
    } finally {
      setBusy(false);
    }
  };

  const onDelete = () => {
    Alert.alert("Delete appointment", "Remove this appointment from the calendar?", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete",
        style: "destructive",
        onPress: async () => {
          try {
            await deleteCalendarEvent(event.id);
            toast.show("Appointment deleted.", "success");
            onChanged();
          } catch (e) {
            toast.show(e instanceof ApiError ? e.message : "Delete failed.", "error");
          }
        },
      },
    ]);
  };

  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable
          style={[styles.card, { backgroundColor: theme.surface, borderColor: theme.border }]}
          onPress={() => {}}
        >
          <View style={styles.titleRow}>
            <View style={[styles.colorBar, { backgroundColor: color }]} />
            <View style={{ flex: 1 }}>
              <Text style={[styles.cardTitle, { color: theme.text }]} numberOfLines={2}>
                {eventTitle(event)}
              </Text>
              <Text style={{ color: theme.muted, fontSize: 13, marginTop: 2 }}>
                {fmtDayShort(start)}
                {event.unscheduled ? " · not scheduled" : ` · ${fmtTime(start)} – ${fmtTime(end)}`}
              </Text>
            </View>
            {stateLabel ? (
              <View style={[styles.stateChip, { backgroundColor: color }]}>
                <Text style={{ color: textOn(color), fontSize: 11, fontWeight: "700" }}>{stateLabel}</Text>
              </View>
            ) : null}
          </View>

          <View style={{ marginTop: 10, gap: 4 }}>
            {!isWo && event.customer_label ? <InfoLine icon="person-outline" text={event.customer_label} /> : null}
            {event.unit_label ? <InfoLine icon="car-outline" text={event.unit_label} /> : null}
            {event.mechanics?.length ? (
              <InfoLine icon="build-outline" text={event.mechanics.map((m) => m.name).join(", ")} />
            ) : event.mechanic_name ? (
              <InfoLine icon="build-outline" text={event.mechanic_name} />
            ) : null}
            {event.presets?.length ? (
              <InfoLine icon="list-outline" text={event.presets.map((p) => p.name).join(", ")} />
            ) : null}
            {event.labor_summary?.length ? (
              <InfoLine
                icon="construct-outline"
                text={event.labor_summary.join(" · ")}
                sub={event.labor_hours ? `Planned ${event.labor_hours} h` : undefined}
              />
            ) : null}
            {worked.length ? (
              <InfoLine
                icon="timer-outline"
                text={worked.map((w) => `${w.name}: ${fmtDuration(w.seconds)}${w.running ? " (running)" : ""}`).join(", ")}
              />
            ) : null}
            {!isWo && event.work_order_id && event.wo_number ? (
              <InfoLine icon="document-text-outline" text={`Linked to WO #${event.wo_number}`} />
            ) : null}
          </View>

          {!isWo && canEdit ? (
            <>
              <Text style={[styles.label, { color: theme.muted }]}>STATUS</Text>
              <View style={styles.chipsRow}>
                {statuses.map((s) => {
                  const active = event.status === s.key;
                  return (
                    <Pressable
                      key={s.key}
                      onPress={() => setStatus(s.key)}
                      style={[
                        styles.statusChip,
                        { backgroundColor: active ? s.color : "transparent", borderColor: s.color },
                      ]}
                    >
                      <Text style={{ color: active ? textOn(s.color) : s.color, fontSize: 12, fontWeight: "700" }}>
                        {s.label}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
            </>
          ) : null}

          <View style={styles.actionsRow}>
            {event.work_order_id ? (
              <Pressable
                style={[styles.actionBtn, { backgroundColor: theme.primary, borderColor: theme.primary }]}
                onPress={() => onOpenWorkOrder(event.work_order_id)}
              >
                <Ionicons name="open-outline" size={16} color="#fff" />
                <Text style={{ color: "#fff", fontWeight: "700" }}>Open work order</Text>
              </Pressable>
            ) : !isWo && canCreateWorkOrder ? (
              <Pressable
                style={[styles.actionBtn, { borderColor: theme.primary }]}
                onPress={() => onCreateWorkOrder(event)}
              >
                <Ionicons name="add-circle-outline" size={16} color={theme.primary} />
                <Text style={{ color: theme.primary, fontWeight: "700" }}>Create work order</Text>
              </Pressable>
            ) : null}
            {!isWo && canEdit ? (
              <Pressable style={[styles.actionBtn, { borderColor: theme.border }]} onPress={() => onEdit(event)}>
                <Ionicons name="pencil-outline" size={16} color={theme.text} />
                <Text style={{ color: theme.text, fontWeight: "600" }}>Edit</Text>
              </Pressable>
            ) : null}
            {!isWo && canDelete ? (
              <Pressable style={[styles.actionBtn, { borderColor: theme.border }]} onPress={onDelete}>
                <Ionicons name="trash-outline" size={16} color={theme.danger} />
                <Text style={{ color: theme.danger, fontWeight: "600" }}>Delete</Text>
              </Pressable>
            ) : null}
          </View>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

function InfoLine({ icon, text, sub }: { icon: string; text: string; sub?: string }) {
  const theme = useTheme();
  return (
    <View style={{ flexDirection: "row", gap: 8, alignItems: "flex-start" }}>
      <Ionicons name={icon as any} size={16} color={theme.muted} style={{ marginTop: 2 }} />
      <View style={{ flex: 1 }}>
        <Text style={{ color: theme.text, fontSize: 14 }}>{text}</Text>
        {sub ? <Text style={{ color: theme.muted, fontSize: 12 }}>{sub}</Text> : null}
      </View>
    </View>
  );
}

// ── форма ────────────────────────────────────────────────────────────

export type EventFormState =
  | { mode: "create"; date: Date; startMin: number; endMin: number }
  | { mode: "edit"; event: CalendarEvent };

export function EventFormModal({
  state,
  statuses,
  onClose,
  onDone,
}: {
  state: EventFormState | null;
  statuses: CalendarStatus[];
  onClose: () => void;
  onDone: () => void;
}) {
  const theme = useTheme();
  const toast = useToast();

  const [title, setTitle] = useState("");
  const [customer, setCustomer] = useState<{ id: string; label: string } | null>(null);
  const [unit, setUnit] = useState<{ id: string; label: string } | null>(null);
  const [units, setUnits] = useState<{ id: string; label: string }[]>([]);
  const [mechanics, setMechanics] = useState<{ id: string; name: string }[]>([]);
  const [mechanic, setMechanic] = useState<{ id: string; name: string } | null>(null);
  const [date, setDate] = useState(() => startOfDay(new Date()));
  const [startMin, setStartMin] = useState(9 * 60);
  const [endMin, setEndMin] = useState(10 * 60);
  const [status, setStatus] = useState("scheduled");
  const [openField, setOpenField] = useState<"start" | "end" | null>(null);
  const [customerModal, setCustomerModal] = useState(false);
  const [busy, setBusy] = useState(false);

  const visible = !!state;
  const isEdit = state?.mode === "edit";

  useEffect(() => {
    if (!state) return;
    setOpenField(null);
    setUnits([]);
    fetchCalendarMechanics().then(setMechanics).catch(() => setMechanics([]));
    if (state.mode === "create") {
      setTitle("");
      setCustomer(null);
      setUnit(null);
      setMechanic(null);
      setDate(startOfDay(state.date));
      setStartMin(state.startMin);
      setEndMin(state.endMin);
      setStatus("scheduled");
      return;
    }
    const ev = state.event;
    const s = eventStart(ev);
    const e = eventEnd(ev);
    setTitle(ev.title && ev.title !== ev.customer_label ? ev.title : "");
    setCustomer(ev.customer_id ? { id: ev.customer_id, label: ev.customer_label || "—" } : null);
    setUnit(ev.unit_id ? { id: ev.unit_id, label: ev.unit_label || "—" } : null);
    setMechanic(ev.mechanic_id ? { id: ev.mechanic_id, name: ev.mechanic_name || "Mechanic" } : null);
    setDate(startOfDay(s));
    setStartMin(s.getHours() * 60 + s.getMinutes());
    setEndMin(Math.max(s.getHours() * 60 + s.getMinutes() + 15, e.getHours() * 60 + e.getMinutes()));
    setStatus(ev.status || "scheduled");
    if (ev.customer_id) {
      fetchCustomerDetails(ev.customer_id)
        .then((d) => setUnits(d.units.map((u) => ({ id: u.id, label: u.label }))))
        .catch(() => setUnits([]));
    }
  }, [state]);

  const pickCustomer = async (c: CustomerRow) => {
    setCustomer({ id: c.id, label: c.company_name || c.contact_name || "—" });
    setCustomerModal(false);
    setUnit(null);
    try {
      const details = await fetchCustomerDetails(c.id);
      setUnits(details.units.map((u) => ({ id: u.id, label: u.label })));
    } catch {
      setUnits([]);
    }
  };

  const changeStart = (min: number) => {
    const duration = Math.max(15, endMin - startMin);
    setStartMin(min);
    setEndMin(Math.min(24 * 60, min + duration));
  };

  const toIso = (min: number) => {
    const d = new Date(date);
    d.setHours(Math.floor(min / 60), min % 60, 0, 0);
    return d.toISOString();
  };

  const submit = async () => {
    if (!customer) {
      toast.show("Select a customer.", "error");
      return;
    }
    if (endMin <= startMin) {
      toast.show("End time must be after start.", "error");
      return;
    }
    setBusy(true);
    try {
      const payload = {
        title: title.trim(),
        start_time: toIso(startMin),
        end_time: toIso(endMin),
        customer_id: customer.id,
        customer_label: customer.label,
        unit_id: unit?.id || "",
        unit_label: unit?.label || "",
        mechanic_id: mechanic?.id || "",
        mechanic_name: mechanic?.name || "",
        status,
      };
      if (state?.mode === "edit") {
        await updateCalendarEvent(state.event.id, payload);
        toast.show("Appointment saved.", "success");
      } else {
        await createCalendarEvent(payload);
        toast.show("Appointment created.", "success");
      }
      onDone();
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Failed to save.", "error");
    } finally {
      setBusy(false);
    }
  };

  const inputStyle = [styles.input, { backgroundColor: theme.surfaceSoft, borderColor: theme.border, color: theme.text }];

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <KeyboardAvoidingView style={styles.sheetBackdrop} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <Pressable style={{ flex: 1 }} onPress={onClose} />
        <View style={[styles.sheet, { backgroundColor: theme.surface, borderColor: theme.border }]}>
          <View style={styles.sheetHeader}>
            <Pressable onPress={onClose} hitSlop={10} disabled={busy}>
              <Ionicons name="close" size={24} color={theme.text} />
            </Pressable>
            <Text style={[styles.cardTitle, { color: theme.text, flex: 1, textAlign: "center" }]}>
              {isEdit ? "Edit appointment" : "New appointment"}
            </Text>
            <Pressable
              style={[styles.saveBtn, { backgroundColor: theme.primary }]}
              onPress={submit}
              disabled={busy}
            >
              {busy ? (
                <ActivityIndicator color="#fff" size="small" />
              ) : (
                <Text style={{ color: "#fff", fontWeight: "700" }}>Save</Text>
              )}
            </Pressable>
          </View>

          <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingBottom: 24 }}>
            <TextInput
              style={[inputStyle, { fontSize: 17, fontWeight: "600" }]}
              value={title}
              onChangeText={setTitle}
              placeholder="Add title (optional)"
              placeholderTextColor={theme.muted}
            />

            <Pressable
              style={[styles.picker, { backgroundColor: theme.surfaceSoft, borderColor: theme.border }]}
              onPress={() => setCustomerModal(true)}
            >
              <Ionicons name="person-outline" size={18} color={theme.muted} />
              <Text style={{ color: customer ? theme.text : theme.muted, flex: 1 }} numberOfLines={1}>
                {customer?.label || "Select customer…"}
              </Text>
              <Ionicons name="chevron-forward" size={16} color={theme.muted} />
            </Pressable>

            {units.length > 0 ? (
              <View style={styles.chipsRow}>
                {units.map((u) => {
                  const active = unit?.id === u.id;
                  return (
                    <Pressable
                      key={u.id}
                      onPress={() => setUnit(active ? null : u)}
                      style={[
                        styles.chip,
                        {
                          backgroundColor: active ? theme.primary : theme.surfaceSoft,
                          borderColor: active ? theme.primary : theme.border,
                        },
                      ]}
                    >
                      <Text style={{ color: active ? "#fff" : theme.text, fontSize: 12 }} numberOfLines={1}>
                        {u.label}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
            ) : null}

            {/* Дата */}
            <View style={[styles.dateRow, { borderColor: theme.border, backgroundColor: theme.surfaceSoft }]}>
              <Pressable onPress={() => setDate((d) => addDays(d, -1))} hitSlop={10} style={{ padding: 6 }}>
                <Ionicons name="chevron-back" size={20} color={theme.text} />
              </Pressable>
              <View style={{ flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6 }}>
                <Ionicons name="calendar-outline" size={16} color={theme.muted} />
                <Text style={{ color: theme.text, fontWeight: "600", fontSize: 15 }}>{fmtDayShort(date)}</Text>
              </View>
              <Pressable onPress={() => setDate((d) => addDays(d, 1))} hitSlop={10} style={{ padding: 6 }}>
                <Ionicons name="chevron-forward" size={20} color={theme.text} />
              </Pressable>
            </View>

            {/* Время */}
            <View style={styles.timeRow}>
              <TimeButton
                label="START"
                value={startMin}
                active={openField === "start"}
                onPress={() => setOpenField((f) => (f === "start" ? null : "start"))}
              />
              <TimeButton
                label="END"
                value={endMin}
                active={openField === "end"}
                onPress={() => setOpenField((f) => (f === "end" ? null : "end"))}
              />
            </View>
            {openField ? (
              <TimePicker
                value={openField === "start" ? startMin : endMin}
                minValue={openField === "end" ? startMin + 15 : 0}
                onChange={(v) => (openField === "start" ? changeStart(v) : setEndMin(v))}
              />
            ) : null}

            {mechanics.length > 0 ? (
              <>
                <Text style={[styles.label, { color: theme.muted }]}>MECHANIC</Text>
                <View style={styles.chipsRow}>
                  {mechanics.map((m) => {
                    const active = mechanic?.id === m.id;
                    return (
                      <Pressable
                        key={m.id}
                        onPress={() => setMechanic(active ? null : m)}
                        style={[
                          styles.chip,
                          {
                            backgroundColor: active ? theme.primary : theme.surfaceSoft,
                            borderColor: active ? theme.primary : theme.border,
                          },
                        ]}
                      >
                        <Text style={{ color: active ? "#fff" : theme.text, fontSize: 12 }}>{m.name}</Text>
                      </Pressable>
                    );
                  })}
                </View>
              </>
            ) : null}

            {isEdit ? (
              <>
                <Text style={[styles.label, { color: theme.muted }]}>STATUS</Text>
                <View style={styles.chipsRow}>
                  {statuses.map((s) => {
                    const active = status === s.key;
                    return (
                      <Pressable
                        key={s.key}
                        onPress={() => setStatus(s.key)}
                        style={[
                          styles.statusChip,
                          { backgroundColor: active ? s.color : "transparent", borderColor: s.color },
                        ]}
                      >
                        <Text style={{ color: active ? textOn(s.color) : s.color, fontSize: 12, fontWeight: "700" }}>
                          {s.label}
                        </Text>
                      </Pressable>
                    );
                  })}
                </View>
              </>
            ) : null}
          </ScrollView>
        </View>
      </KeyboardAvoidingView>

      <SearchPickerModal<CustomerRow>
        visible={customerModal}
        onClose={() => setCustomerModal(false)}
        title="Select customer"
        placeholder="Search customers…"
        search={(q) => fetchCustomers(q, 1).then((d) => d.items)}
        renderLabel={(c) => c.company_name || c.contact_name || "—"}
        onPick={pickCustomer}
      />
    </Modal>
  );
}

function TimeButton({
  label,
  value,
  active,
  onPress,
}: {
  label: string;
  value: number;
  active: boolean;
  onPress: () => void;
}) {
  const theme = useTheme();
  return (
    <View style={{ flex: 1 }}>
      <Text style={[styles.label, { color: theme.muted }]}>{label}</Text>
      <Pressable
        onPress={onPress}
        style={[
          styles.timeBtn,
          { backgroundColor: theme.surfaceSoft, borderColor: active ? theme.primary : theme.border },
        ]}
      >
        <Ionicons name="time-outline" size={16} color={active ? theme.primary : theme.muted} />
        <Text style={{ color: theme.text, fontSize: 17, fontWeight: "700" }}>{fmtMinutes(value)}</Text>
      </Pressable>
    </View>
  );
}

/** Часы — горизонтальная лента, минуты — четыре чипа с шагом 15. */
function TimePicker({
  value,
  minValue,
  onChange,
}: {
  value: number;
  minValue: number;
  onChange: (minutes: number) => void;
}) {
  const theme = useTheme();
  const hour = Math.floor(value / 60);
  const minute = value % 60;
  const hours = Array.from({ length: 24 }, (_, h) => h);
  const minutes = [0, 15, 30, 45];
  const pick = (h: number, m: number) => onChange(Math.max(minValue, h * 60 + m));
  return (
    <View style={[styles.timePicker, { borderColor: theme.border, backgroundColor: theme.surfaceSoft }]}>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={{ gap: 6, paddingHorizontal: 8 }}
        contentOffset={{ x: Math.max(0, hour - 3) * 46, y: 0 }}
      >
        {hours.map((h) => {
          const disabled = h * 60 + 45 < minValue;
          const active = h === hour;
          return (
            <Pressable
              key={h}
              disabled={disabled}
              onPress={() => pick(h, minute)}
              style={[
                styles.hourChip,
                { backgroundColor: active ? theme.primary : theme.surface, borderColor: active ? theme.primary : theme.border },
                disabled && { opacity: 0.35 },
              ]}
            >
              <Text style={{ color: active ? "#fff" : theme.text, fontWeight: "700" }}>{String(h).padStart(2, "0")}</Text>
            </Pressable>
          );
        })}
      </ScrollView>
      <View style={{ flexDirection: "row", gap: 6, paddingHorizontal: 8, marginTop: 8 }}>
        {minutes.map((m) => {
          const active = m === minute;
          const disabled = hour * 60 + m < minValue;
          return (
            <Pressable
              key={m}
              disabled={disabled}
              onPress={() => pick(hour, m)}
              style={[
                styles.minuteChip,
                { backgroundColor: active ? theme.primary : theme.surface, borderColor: active ? theme.primary : theme.border },
                disabled && { opacity: 0.35 },
              ]}
            >
              <Text style={{ color: active ? "#fff" : theme.text, fontWeight: "700" }}>:{String(m).padStart(2, "0")}</Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.5)",
    justifyContent: "center",
    padding: 20,
  },
  card: { borderWidth: 1, borderRadius: 16, padding: 18 },
  titleRow: { flexDirection: "row", gap: 10, alignItems: "flex-start" },
  colorBar: { width: 5, alignSelf: "stretch", borderRadius: 3, minHeight: 36 },
  cardTitle: { fontSize: 16, fontWeight: "800" },
  stateChip: { borderRadius: 999, paddingHorizontal: 9, paddingVertical: 4 },
  label: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: 12, marginBottom: 4 },
  chipsRow: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 8 },
  chip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 5, maxWidth: "100%" },
  statusChip: { borderWidth: 1.5, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 6 },
  actionsRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 16 },
  actionBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    borderWidth: 1,
    borderRadius: 10,
    paddingVertical: 10,
    paddingHorizontal: 12,
    flexGrow: 1,
  },
  sheetBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "flex-end" },
  sheet: {
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    borderWidth: 1,
    paddingHorizontal: 16,
    paddingTop: 10,
    maxHeight: "92%",
  },
  sheetHeader: { flexDirection: "row", alignItems: "center", gap: 10, paddingBottom: 8 },
  saveBtn: { borderRadius: 999, paddingHorizontal: 16, paddingVertical: 7, minWidth: 64, alignItems: "center" },
  input: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10, fontSize: 15, marginTop: 6 },
  picker: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 12,
    marginTop: 8,
  },
  dateRow: {
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1,
    borderRadius: 10,
    marginTop: 10,
    paddingHorizontal: 4,
  },
  timeRow: { flexDirection: "row", gap: 10 },
  timeBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    borderWidth: 1.5,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  timePicker: { borderWidth: 1, borderRadius: 10, paddingVertical: 10, marginTop: 8 },
  hourChip: { width: 40, height: 36, borderRadius: 8, borderWidth: 1, alignItems: "center", justifyContent: "center" },
  minuteChip: { flex: 1, height: 36, borderRadius: 8, borderWidth: 1, alignItems: "center", justifyContent: "center" },
});
