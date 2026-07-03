/**
 * Календарь в стиле Google Calendar (мобильный Day view):
 * лента дней недели → таймлайн выбранного дня с часовой сеткой,
 * событList-блоки по времени с цветом статуса, красная линия «сейчас»,
 * создание тапом по пустому слоту или FAB, деталка события со сменой
 * статуса и удалением.
 */
import Ionicons from "@expo/vector-icons/Ionicons";
import { Stack, useFocusEffect } from "expo-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { useAuth } from "@/context/auth";
import { useToast } from "@/context/toast";
import {
  ApiError,
  CalendarEvent,
  CalendarStatus,
  CustomerRow,
  createCalendarEvent,
  deleteCalendarEvent,
  fetchCalendarEvents,
  fetchCalendarMechanics,
  fetchCalendarStatuses,
  fetchCustomerDetails,
  fetchCustomers,
  updateCalendarEvent,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

const HOUR_H = 64;
const GUTTER_W = 52;
const DAY_MS = 24 * 60 * 60 * 1000;

function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function startOfWeek(d: Date): Date {
  const out = startOfDay(d);
  out.setDate(out.getDate() - ((out.getDay() + 6) % 7)); // понедельник
  return out;
}

function addDays(d: Date, n: number): Date {
  const out = new Date(d);
  out.setDate(out.getDate() + n);
  return out;
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function fmtTime(d: Date): string {
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

const DAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"];
const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

interface PositionedEvent {
  ev: CalendarEvent;
  top: number;
  height: number;
  col: number;
  cols: number;
}

/** Раскладка пересекающихся событий по колонкам (как в Google Calendar). */
function layoutEvents(events: CalendarEvent[]): PositionedEvent[] {
  const items = events
    .filter((e) => e.start_time && e.end_time)
    .map((e) => {
      const s = new Date(e.start_time);
      const en = new Date(e.end_time);
      const startMin = s.getHours() * 60 + s.getMinutes();
      const endMin = Math.max(startMin + 20, en.getHours() * 60 + en.getMinutes() || startMin + 30);
      return { ev: e, startMin, endMin };
    })
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
        height: Math.max(26, ((p.endMin - p.startMin) / 60) * HOUR_H - 2),
        col: p.col,
        cols,
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

export default function CalendarScreen() {
  const theme = useTheme();
  const toast = useToast();
  const { session } = useAuth();

  const [selectedDay, setSelectedDay] = useState(() => startOfDay(new Date()));
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [statuses, setStatuses] = useState<CalendarStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [createHour, setCreateHour] = useState(9);
  const [detailEvent, setDetailEvent] = useState<CalendarEvent | null>(null);
  const [nowTick, setNowTick] = useState(Date.now());

  const scrollRef = useRef<ScrollView | null>(null);
  const didAutoScroll = useRef(false);

  const weekStart = startOfWeek(selectedDay);
  const days = Array.from({ length: 7 }, (_, i) => addDays(weekStart, i));
  const today = startOfDay(new Date());
  const isToday = sameDay(selectedDay, today);

  const canDelete =
    (session?.permissions || []).includes("calendar.delete") || session?.user.role === "owner";

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const from = weekStart;
      const to = addDays(weekStart, 7);
      const [list, sts] = await Promise.all([
        fetchCalendarEvents(from.toISOString(), to.toISOString()),
        statuses.length ? Promise.resolve(statuses) : fetchCalendarStatuses().catch(() => []),
      ]);
      setEvents(list);
      if (!statuses.length && sts.length) setStatuses(sts);
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Failed to load events.", "error");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [weekStart.getTime()]);

  useFocusEffect(
    useCallback(() => {
      load();
      const t = setInterval(() => setNowTick(Date.now()), 60_000);
      return () => clearInterval(t);
    }, [load])
  );

  // Автоскролл к «сейчас − 1ч» (или к 07:00) один раз.
  useEffect(() => {
    if (loading || didAutoScroll.current || !scrollRef.current) return;
    didAutoScroll.current = true;
    const hour = isToday ? Math.max(0, new Date().getHours() - 1) : 7;
    setTimeout(() => scrollRef.current?.scrollTo({ y: hour * HOUR_H, animated: false }), 50);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading]);

  const statusColor = useCallback(
    (key: string) => statuses.find((s) => s.key === key)?.color || "#1a73e8",
    [statuses]
  );

  const dayEvents = useMemo(
    () => events.filter((e) => e.start_time && sameDay(new Date(e.start_time), selectedDay)),
    [events, selectedDay]
  );
  const positioned = useMemo(() => layoutEvents(dayEvents), [dayEvents]);

  const eventsCountByDay = useMemo(() => {
    const map = new Map<number, number>();
    for (const e of events) {
      if (!e.start_time) continue;
      const k = startOfDay(new Date(e.start_time)).getTime();
      map.set(k, (map.get(k) || 0) + 1);
    }
    return map;
  }, [events]);

  const now = new Date(nowTick);
  const nowTop = ((now.getHours() * 60 + now.getMinutes()) / 60) * HOUR_H;

  return (
    <View style={{ flex: 1, backgroundColor: theme.bg }}>
      <Stack.Screen
        options={{
          title: `${MONTHS[selectedDay.getMonth()]} ${selectedDay.getFullYear()}`,
          headerRight: () => (
            <Pressable
              onPress={() => setSelectedDay(today)}
              hitSlop={8}
              style={{ paddingHorizontal: 10 }}
            >
              <Ionicons name="today-outline" size={22} color={theme.primary} />
            </Pressable>
          ),
        }}
      />

      {/* Лента дней недели */}
      <View style={[styles.weekStrip, { borderBottomColor: theme.border, backgroundColor: theme.surface }]}>
        <Pressable onPress={() => setSelectedDay((d) => addDays(d, -7))} hitSlop={8}>
          <Ionicons name="chevron-back" size={20} color={theme.muted} />
        </Pressable>
        {days.map((day, i) => {
          const selected = sameDay(day, selectedDay);
          const isTodayChip = sameDay(day, today);
          const hasEvents = (eventsCountByDay.get(day.getTime()) || 0) > 0;
          return (
            <Pressable key={i} style={styles.dayChip} onPress={() => setSelectedDay(day)}>
              <Text style={{ color: theme.muted, fontSize: 11, fontWeight: "600" }}>
                {DAY_LETTERS[i]}
              </Text>
              <View
                style={[
                  styles.dayNum,
                  selected && { backgroundColor: theme.primary },
                ]}
              >
                <Text
                  style={{
                    color: selected ? "#fff" : isTodayChip ? theme.primary : theme.text,
                    fontWeight: isTodayChip || selected ? "800" : "500",
                    fontSize: 15,
                  }}
                >
                  {day.getDate()}
                </Text>
              </View>
              <View
                style={[
                  styles.dot,
                  { backgroundColor: hasEvents ? theme.primary : "transparent" },
                ]}
              />
            </Pressable>
          );
        })}
        <Pressable onPress={() => setSelectedDay((d) => addDays(d, 7))} hitSlop={8}>
          <Ionicons name="chevron-forward" size={20} color={theme.muted} />
        </Pressable>
      </View>

      {/* Таймлайн дня */}
      {loading ? (
        <ActivityIndicator color={theme.primary} size="large" style={{ marginTop: 48 }} />
      ) : (
        <ScrollView ref={scrollRef}>
          <View style={{ height: 24 * HOUR_H, flexDirection: "row" }}>
            {/* Левая колонка времени */}
            <View style={{ width: GUTTER_W }}>
              {Array.from({ length: 24 }, (_, h) => (
                <View key={h} style={{ height: HOUR_H }}>
                  {h > 0 ? (
                    <Text style={[styles.hourLabel, { color: theme.muted }]}>
                      {String(h).padStart(2, "0")}:00
                    </Text>
                  ) : null}
                </View>
              ))}
            </View>

            {/* Сетка + события */}
            <View style={{ flex: 1 }}>
              {Array.from({ length: 24 }, (_, h) => (
                <Pressable
                  key={h}
                  style={[styles.hourSlot, { borderTopColor: theme.border, height: HOUR_H }]}
                  onPress={() => {
                    setCreateHour(h);
                    setCreateOpen(true);
                  }}
                />
              ))}

              {positioned.map((p) => {
                const color = statusColor(p.ev.status);
                const widthPct = 100 / p.cols;
                const start = new Date(p.ev.start_time);
                const end = new Date(p.ev.end_time);
                return (
                  <Pressable
                    key={p.ev.id}
                    onPress={() => setDetailEvent(p.ev)}
                    style={[
                      styles.eventBlock,
                      {
                        top: p.top,
                        height: p.height,
                        left: `${p.col * widthPct}%`,
                        width: `${widthPct}%`,
                        backgroundColor: color,
                      },
                    ]}
                  >
                    <Text style={styles.eventTitle} numberOfLines={1}>
                      {p.ev.title || p.ev.customer_label || "Appointment"}
                    </Text>
                    {p.height >= 38 ? (
                      <Text style={styles.eventTime} numberOfLines={1}>
                        {fmtTime(start)} – {fmtTime(end)}
                        {p.ev.unit_label ? ` · ${p.ev.unit_label}` : ""}
                      </Text>
                    ) : null}
                  </Pressable>
                );
              })}

              {/* Красная линия «сейчас» */}
              {isToday ? (
                <View pointerEvents="none" style={[styles.nowLine, { top: nowTop }]}>
                  <View style={styles.nowDot} />
                  <View style={styles.nowBar} />
                </View>
              ) : null}
            </View>
          </View>
        </ScrollView>
      )}

      {/* FAB */}
      <Pressable
        style={[styles.fab, { backgroundColor: theme.primary }]}
        onPress={() => {
          setCreateHour(isToday ? Math.min(23, new Date().getHours() + 1) : 9);
          setCreateOpen(true);
        }}
      >
        <Ionicons name="add" size={28} color="#fff" />
      </Pressable>

      <CreateEventModal
        visible={createOpen}
        date={selectedDay}
        hour={createHour}
        onClose={() => setCreateOpen(false)}
        onDone={() => {
          setCreateOpen(false);
          load();
        }}
      />

      <EventDetailModal
        event={detailEvent}
        statuses={statuses}
        canDelete={canDelete}
        onClose={() => setDetailEvent(null)}
        onChanged={() => {
          setDetailEvent(null);
          load();
        }}
      />
    </View>
  );
}

// ── деталка события ─────────────────────────────────────────────────

function EventDetailModal({
  event,
  statuses,
  canDelete,
  onClose,
  onChanged,
}: {
  event: CalendarEvent | null;
  statuses: CalendarStatus[];
  canDelete: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const theme = useTheme();
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  if (!event) return null;
  const start = new Date(event.start_time);
  const end = new Date(event.end_time);

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
          <Text style={[styles.cardTitle, { color: theme.text }]} numberOfLines={2}>
            {event.title || event.customer_label || "Appointment"}
          </Text>
          <Text style={{ color: theme.muted, fontSize: 14 }}>
            {fmtTime(start)} – {fmtTime(end)}
          </Text>
          {event.unit_label ? (
            <Text style={{ color: theme.text, fontSize: 14, marginTop: 6 }}>🚛 {event.unit_label}</Text>
          ) : null}
          {event.mechanic_name ? (
            <Text style={{ color: theme.text, fontSize: 14, marginTop: 2 }}>🔧 {event.mechanic_name}</Text>
          ) : null}
          {event.presets?.length ? (
            <Text style={{ color: theme.muted, fontSize: 13, marginTop: 2 }}>
              {event.presets.map((p) => p.name).join(", ")}
            </Text>
          ) : null}

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
                    {
                      backgroundColor: active ? s.color : "transparent",
                      borderColor: s.color,
                    },
                  ]}
                >
                  <Text style={{ color: active ? "#fff" : s.color, fontSize: 12, fontWeight: "700" }}>
                    {s.label}
                  </Text>
                </Pressable>
              );
            })}
          </View>

          {canDelete ? (
            <Pressable style={[styles.deleteBtn, { borderColor: theme.border }]} onPress={onDelete}>
              <Ionicons name="trash-outline" size={16} color={theme.danger} />
              <Text style={{ color: theme.danger, fontWeight: "600" }}>Delete</Text>
            </Pressable>
          ) : null}
        </Pressable>
      </Pressable>
    </Modal>
  );
}

// ── создание события ────────────────────────────────────────────────

function CreateEventModal({
  visible,
  date,
  hour,
  onClose,
  onDone,
}: {
  visible: boolean;
  date: Date;
  hour: number;
  onClose: () => void;
  onDone: () => void;
}) {
  const theme = useTheme();
  const toast = useToast();

  const [customer, setCustomer] = useState<{ id: string; label: string } | null>(null);
  const [unit, setUnit] = useState<{ id: string; label: string } | null>(null);
  const [units, setUnits] = useState<{ id: string; label: string }[]>([]);
  const [mechanics, setMechanics] = useState<{ id: string; name: string }[]>([]);
  const [mechanic, setMechanic] = useState<{ id: string; name: string } | null>(null);
  const [start, setStart] = useState("09:00");
  const [end, setEnd] = useState("10:00");
  const [customerModal, setCustomerModal] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (visible) {
      setCustomer(null);
      setUnit(null);
      setUnits([]);
      setMechanic(null);
      setStart(`${String(hour).padStart(2, "0")}:00`);
      setEnd(`${String(Math.min(23, hour + 1)).padStart(2, "0")}:00`);
      fetchCalendarMechanics().then(setMechanics).catch(() => setMechanics([]));
    }
  }, [visible, hour]);

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

  const submit = async () => {
    if (!customer) {
      toast.show("Select a customer.", "error");
      return;
    }
    const timeRe = /^\d{1,2}:\d{2}$/;
    if (!timeRe.test(start.trim()) || !timeRe.test(end.trim())) {
      toast.show("Time must be HH:MM.", "error");
      return;
    }
    const mk = (t: string) => {
      const [h, m] = t.trim().split(":").map(Number);
      const d = new Date(date);
      d.setHours(h, m, 0, 0);
      return d.toISOString();
    };
    const startIso = mk(start);
    const endIso = mk(end);
    if (endIso <= startIso) {
      toast.show("End time must be after start.", "error");
      return;
    }
    setBusy(true);
    try {
      await createCalendarEvent({
        start_time: startIso,
        end_time: endIso,
        customer_id: customer.id,
        customer_label: customer.label,
        unit_id: unit?.id,
        unit_label: unit?.label,
        mechanic_id: mechanic?.id,
        mechanic_name: mechanic?.name,
        status: "scheduled",
      });
      toast.show("Appointment created.", "success");
      onDone();
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Failed to create.", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <KeyboardAvoidingView
        style={styles.backdrop}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <View style={[styles.card, { backgroundColor: theme.surface, borderColor: theme.border }]}>
          <Text style={[styles.cardTitle, { color: theme.text }]}>
            New appointment · {date.getDate()} {MONTHS[date.getMonth()].slice(0, 3)}
          </Text>

          <Pressable
            style={[styles.picker, { backgroundColor: theme.surfaceSoft, borderColor: theme.border }]}
            onPress={() => setCustomerModal(true)}
          >
            <Text style={{ color: customer ? theme.text : theme.muted }}>
              {customer?.label || "Select customer…"}
            </Text>
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

          <View style={styles.timeRow}>
            <View style={{ flex: 1 }}>
              <Text style={[styles.label, { color: theme.muted }]}>START</Text>
              <TextInput
                style={[styles.input, { backgroundColor: theme.surfaceSoft, borderColor: theme.border, color: theme.text }]}
                value={start}
                onChangeText={setStart}
                placeholder="09:00"
                placeholderTextColor={theme.muted}
              />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={[styles.label, { color: theme.muted }]}>END</Text>
              <TextInput
                style={[styles.input, { backgroundColor: theme.surfaceSoft, borderColor: theme.border, color: theme.text }]}
                value={end}
                onChangeText={setEnd}
                placeholder="10:00"
                placeholderTextColor={theme.muted}
              />
            </View>
          </View>

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

          <View style={styles.btnRow}>
            <Pressable style={[styles.btn, { borderColor: theme.border }]} onPress={onClose} disabled={busy}>
              <Text style={{ color: theme.text, fontWeight: "600" }}>Cancel</Text>
            </Pressable>
            <Pressable
              style={[styles.btn, styles.btnPrimary, { backgroundColor: theme.primary }]}
              onPress={submit}
              disabled={busy}
            >
              {busy ? (
                <ActivityIndicator color="#fff" size="small" />
              ) : (
                <Text style={{ color: "#fff", fontWeight: "700" }}>Create</Text>
              )}
            </Pressable>
          </View>
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

const styles = StyleSheet.create({
  weekStrip: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 8,
    paddingVertical: 8,
    borderBottomWidth: 1,
  },
  dayChip: { alignItems: "center", gap: 2, width: 36 },
  dayNum: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: "center",
    justifyContent: "center",
  },
  dot: { width: 4, height: 4, borderRadius: 2 },
  hourLabel: { fontSize: 11, marginTop: -7, textAlign: "right", paddingRight: 8 },
  hourSlot: { borderTopWidth: StyleSheet.hairlineWidth },
  eventBlock: {
    position: "absolute",
    borderRadius: 6,
    paddingHorizontal: 6,
    paddingVertical: 3,
    marginRight: 2,
    overflow: "hidden",
  },
  eventTitle: { color: "#fff", fontSize: 12, fontWeight: "700" },
  eventTime: { color: "rgba(255,255,255,0.9)", fontSize: 11 },
  nowLine: { position: "absolute", left: 0, right: 0, flexDirection: "row", alignItems: "center" },
  nowDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: "#ea4335", marginLeft: -5 },
  nowBar: { flex: 1, height: 2, backgroundColor: "#ea4335" },
  fab: {
    position: "absolute",
    right: 20,
    bottom: 28,
    width: 56,
    height: 56,
    borderRadius: 28,
    alignItems: "center",
    justifyContent: "center",
    elevation: 6,
    shadowColor: "#000",
    shadowOpacity: 0.3,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 3 },
  },
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.5)",
    justifyContent: "center",
    padding: 20,
  },
  card: { borderWidth: 1, borderRadius: 16, padding: 18 },
  cardTitle: { fontSize: 16, fontWeight: "800", marginBottom: 6 },
  picker: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 12, marginTop: 6 },
  chipsRow: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 8 },
  chip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 5, maxWidth: "100%" },
  statusChip: { borderWidth: 1.5, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 6 },
  timeRow: { flexDirection: "row", gap: 10, marginTop: 8 },
  label: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: 10, marginBottom: 4 },
  input: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10, fontSize: 15 },
  btnRow: { flexDirection: "row", gap: 10, marginTop: 18 },
  deleteBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    borderWidth: 1,
    borderRadius: 10,
    paddingVertical: 11,
    marginTop: 16,
  },
  btn: { flex: 1, borderWidth: 1, borderRadius: 10, paddingVertical: 12, alignItems: "center" },
  btnPrimary: { borderWidth: 0 },
});
