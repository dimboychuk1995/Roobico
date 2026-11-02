/**
 * Календарь (вкладка, доступна всем с calendar.view — и механику):
 * виды Schedule / Day / 3 Day / Week / Month как в Google Calendar,
 * выпадающий месяц по тапу на заголовок, фильтр «только мои», записи
 * календаря + WO-блоки (сессии таймеров и «не расписанные» WO) с
 * сервера. Данные кэшируются по загруженным диапазонам недель.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import Ionicons from "@expo/vector-icons/Ionicons";
import { useFocusEffect, useRouter } from "expo-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import {
  CalendarView,
  DAY_LETTERS,
  DateRange,
  MONTHS,
  VIEWS,
  addDays,
  dayKey,
  eventStart,
  isMine,
  makeColorOf,
  rangeCovers,
  rangeForView,
  sameDay,
  shiftSelected,
  startOfDay,
  startOfWeek,
  visibleDays,
} from "@/components/calendar/dates";
import { EventDetailModal, EventFormModal, EventFormState } from "@/components/calendar/event-modals";
import { MonthPicker } from "@/components/calendar/month-picker";
import { MonthView } from "@/components/calendar/month-view";
import { ScheduleView } from "@/components/calendar/schedule-view";
import { TimeGrid } from "@/components/calendar/time-grid";
import { useAuth, useHasPermission, useIsMechanic } from "@/context/auth";
import { useToast } from "@/context/toast";
import {
  ApiError,
  CalendarEvent,
  CalendarStatus,
  fetchCalendarEvents,
  fetchCalendarStatuses,
  fetchCalendarWoStates,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

const VIEW_KEY = "roobico.calendar.view";
const MINE_KEY = "roobico.calendar.mine";

export default function CalendarScreen() {
  const theme = useTheme();
  const toast = useToast();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { session } = useAuth();
  const isMechanic = useIsMechanic();
  const canCreate = useHasPermission("calendar.create");
  const canEdit = useHasPermission("calendar.edit");
  const canDelete = useHasPermission("calendar.delete");
  const canCreateWo = useHasPermission("work_orders.create") && !isMechanic;
  const userId = session?.user.id || "";

  const [view, setView] = useState<CalendarView>("day");
  const [prefsReady, setPrefsReady] = useState(false);
  const [selectedDay, setSelectedDay] = useState(() => startOfDay(new Date()));
  const [mine, setMine] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerMonth, setPickerMonth] = useState(() => startOfDay(new Date()));
  const [viewMenuOpen, setViewMenuOpen] = useState(false);
  const [scheduleWeeks, setScheduleWeeks] = useState(5);
  const [scheduleEarlier, setScheduleEarlier] = useState(0);

  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [loadedRanges, setLoadedRanges] = useState<DateRange[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [statuses, setStatuses] = useState<CalendarStatus[]>([]);
  const [woStates, setWoStates] = useState<CalendarStatus[]>([]);
  const [formState, setFormState] = useState<EventFormState | null>(null);
  const [detailEvent, setDetailEvent] = useState<CalendarEvent | null>(null);
  const [nowTick, setNowTick] = useState(Date.now());
  const fetchSeq = useRef(0);

  // Последний вид и фильтр «мои» помним между запусками.
  useEffect(() => {
    (async () => {
      try {
        const [v, m] = await Promise.all([AsyncStorage.getItem(VIEW_KEY), AsyncStorage.getItem(MINE_KEY)]);
        if (v && VIEWS.some((x) => x.key === v)) setView(v as CalendarView);
        if (m === "1") setMine(true);
      } finally {
        setPrefsReady(true);
      }
    })();
  }, []);
  const changeView = (v: CalendarView) => {
    setView(v);
    setViewMenuOpen(false);
    AsyncStorage.setItem(VIEW_KEY, v).catch(() => {});
  };
  const toggleMine = () => {
    setMine((m) => {
      AsyncStorage.setItem(MINE_KEY, m ? "0" : "1").catch(() => {});
      return !m;
    });
  };

  const today = startOfDay(new Date(nowTick));
  const days = useMemo(() => visibleDays(view, selectedDay), [view, selectedDay]);
  const range = useMemo<DateRange>(() => {
    if (view === "schedule") {
      const start = addDays(startOfDay(selectedDay), -7 * scheduleEarlier);
      return { start, end: addDays(startOfDay(selectedDay), 7 * scheduleWeeks) };
    }
    return rangeForView(view, selectedDay);
  }, [view, selectedDay, scheduleWeeks, scheduleEarlier]);
  // Для точек в выпадающем месяце подгружаем и его сетку.
  const pickerRange = useMemo<DateRange | null>(
    () => (pickerOpen ? rangeForView("month", pickerMonth) : null),
    [pickerOpen, pickerMonth]
  );

  const isLoaded = useCallback(
    (r: DateRange) => loadedRanges.some((lr) => rangeCovers(lr, r)),
    [loadedRanges]
  );

  /** Загрузить диапазон (всегда целыми неделями) и влить в кэш. */
  const loadRange = useCallback(
    async (r: DateRange, opts: { force?: boolean; silent?: boolean } = {}) => {
      const start = startOfWeek(r.start);
      const end = addDays(startOfWeek(addDays(r.end, -1)), 7);
      const seq = ++fetchSeq.current;
      if (!opts.silent) setLoading(true);
      try {
        const [list, sts, wos] = await Promise.all([
          fetchCalendarEvents(start.toISOString(), end.toISOString(), { includeWorkOrders: true }),
          statuses.length ? Promise.resolve(statuses) : fetchCalendarStatuses().catch(() => [] as CalendarStatus[]),
          woStates.length ? Promise.resolve(woStates) : fetchCalendarWoStates().catch(() => [] as CalendarStatus[]),
        ]);
        if (!statuses.length && sts.length) setStatuses(sts);
        if (!woStates.length && wos.length) setWoStates(wos);
        setEvents((prev) => {
          // Заменяем всё, что попадает в перезагруженный диапазон.
          const keep = prev.filter((e) => {
            const t = eventStart(e).getTime();
            return t < start.getTime() || t >= end.getTime();
          });
          const seen = new Set(keep.map((e) => e.id));
          return keep.concat(list.filter((e) => !seen.has(e.id)));
        });
        setLoadedRanges((prev) => (opts.force ? [{ start, end }] : prev.concat([{ start, end }])));
      } catch (e) {
        toast.show(e instanceof ApiError ? e.message : "Failed to load calendar.", "error");
      } finally {
        if (seq === fetchSeq.current) setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [statuses.length, woStates.length]
  );

  // Диапазон вида / пикера не покрыт кэшем — догружаем.
  useEffect(() => {
    if (!prefsReady) return;
    if (!isLoaded(range)) loadRange(range);
  }, [prefsReady, range, isLoaded, loadRange]);
  useEffect(() => {
    if (pickerRange && !isLoaded(pickerRange)) loadRange(pickerRange, { silent: true });
  }, [pickerRange, isLoaded, loadRange]);

  /**
   * Перезагрузка текущего диапазона (после правок и при возврате на экран):
   * force оставляет в кэше только его, остальное догрузится при навигации.
   * Кэш заранее не сбрасываем — иначе эффект догрузки выстрелит вторым
   * параллельным запросом за тем же диапазоном.
   */
  const reload = useCallback(
    (silent = true) => loadRange(range, { force: true, silent }),
    [loadRange, range]
  );

  // Первый заход грузит эффект догрузки (со спиннером); тут — только
  // повторные возвраты на вкладку.
  const firstFocus = useRef(true);
  useFocusEffect(
    useCallback(() => {
      if (prefsReady) {
        if (firstFocus.current) firstFocus.current = false;
        else reload(true);
      }
      const t = setInterval(() => setNowTick(Date.now()), 60_000);
      return () => clearInterval(t);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [prefsReady])
  );

  const onRefresh = async () => {
    setRefreshing(true);
    try {
      await reload(true);
    } finally {
      setRefreshing(false);
    }
  };

  const colorOf = useMemo(() => makeColorOf(statuses, woStates), [statuses, woStates]);

  // Что рисуем: без «перекрытых фактами» планов, с фильтром «мои».
  const visibleEvents = useMemo(
    () => events.filter((e) => !e.superseded && (!mine || isMine(e, userId))),
    [events, mine, userId]
  );
  const eventDays = useMemo(() => {
    const s = new Set<string>();
    for (const e of visibleEvents) if (e.start_time) s.add(dayKey(eventStart(e)));
    return s;
  }, [visibleEvents]);

  const goto = (dir: 1 | -1) => setSelectedDay((d) => shiftSelected(view, d, dir));
  const openDay = (day: Date) => {
    setSelectedDay(startOfDay(day));
    changeView("day");
  };
  const openCreate = (day: Date, startMin: number) => {
    if (!canCreate) return;
    setFormState({ mode: "create", date: day, startMin, endMin: Math.min(24 * 60, startMin + 60) });
  };
  const openWorkOrder = (id: string) => {
    setDetailEvent(null);
    router.push({ pathname: "/work-order/[id]", params: { id } });
  };
  const createWorkOrderFrom = (ev: CalendarEvent) => {
    setDetailEvent(null);
    router.push({
      pathname: "/work-order-form",
      params: { customerId: ev.customer_id || "", unitId: ev.unit_id || "", calendarEventId: ev.id },
    });
  };

  const titleMonth = view === "week" || view === "3day" ? days[0] : selectedDay;
  const title = `${MONTHS[titleMonth.getMonth()]} ${titleMonth.getFullYear()}`;
  const activeView = VIEWS.find((v) => v.key === view) || VIEWS[1];
  const initialLoading = loading && !loadedRanges.length;

  return (
    <View style={{ flex: 1, backgroundColor: theme.bg }}>
      {/* Шапка в стиле Google Calendar */}
      <View
        style={[
          styles.header,
          { paddingTop: insets.top + 6, backgroundColor: theme.surface, borderBottomColor: theme.border },
        ]}
      >
        <Pressable onPress={() => setViewMenuOpen(true)} hitSlop={8} style={styles.headerBtn}>
          <Ionicons name={activeView.icon as any} size={22} color={theme.text} />
        </Pressable>
        <Pressable
          onPress={() => {
            setPickerMonth(selectedDay);
            setPickerOpen((o) => !o);
          }}
          style={styles.titleBtn}
          hitSlop={6}
        >
          <Text style={[styles.title, { color: theme.text }]} numberOfLines={1}>
            {title}
          </Text>
          <Ionicons name={pickerOpen ? "chevron-up" : "chevron-down"} size={16} color={theme.muted} />
          {loading && !initialLoading ? <ActivityIndicator size="small" color={theme.primary} /> : null}
        </Pressable>
        <Pressable onPress={toggleMine} hitSlop={8} style={styles.headerBtn}>
          <Ionicons name={mine ? "person" : "person-outline"} size={21} color={mine ? theme.primary : theme.text} />
        </Pressable>
        <Pressable onPress={() => setSelectedDay(today)} hitSlop={8} style={styles.headerBtn}>
          <View style={[styles.todayIcon, { borderColor: theme.text }]}>
            <Text style={{ color: theme.text, fontSize: 10, fontWeight: "800", lineHeight: 12 }}>{today.getDate()}</Text>
          </View>
        </Pressable>
        {isMechanic ? (
          <Pressable onPress={() => router.push("/settings")} hitSlop={8} style={styles.headerBtn}>
            <Ionicons name="settings-outline" size={21} color={theme.muted} />
          </Pressable>
        ) : null}
      </View>

      {/* Лента недели — только в Day view */}
      {view === "day" ? (
        <View style={[styles.weekStrip, { borderBottomColor: theme.border, backgroundColor: theme.surface }]}>
          <Pressable onPress={() => setSelectedDay((d) => addDays(d, -7))} hitSlop={8}>
            <Ionicons name="chevron-back" size={20} color={theme.muted} />
          </Pressable>
          {Array.from({ length: 7 }, (_, i) => addDays(startOfWeek(selectedDay), i)).map((day, i) => {
            const selected = sameDay(day, selectedDay);
            const isToday = sameDay(day, today);
            const has = eventDays.has(dayKey(day));
            return (
              <Pressable key={i} style={styles.dayChip} onPress={() => setSelectedDay(day)}>
                <Text style={{ color: isToday ? theme.primary : theme.muted, fontSize: 11, fontWeight: "600" }}>
                  {DAY_LETTERS[i]}
                </Text>
                <View
                  style={[
                    styles.dayNum,
                    selected && { backgroundColor: theme.primary },
                    !selected && isToday && { borderWidth: 1.5, borderColor: theme.primary },
                  ]}
                >
                  <Text
                    style={{
                      color: selected ? "#fff" : isToday ? theme.primary : theme.text,
                      fontWeight: isToday || selected ? "800" : "500",
                      fontSize: 15,
                    }}
                  >
                    {day.getDate()}
                  </Text>
                </View>
                <View style={[styles.dot, { backgroundColor: has ? theme.primary : "transparent" }]} />
              </Pressable>
            );
          })}
          <Pressable onPress={() => setSelectedDay((d) => addDays(d, 7))} hitSlop={8}>
            <Ionicons name="chevron-forward" size={20} color={theme.muted} />
          </Pressable>
        </View>
      ) : null}

      <View style={{ flex: 1 }}>
        {initialLoading ? (
          <ActivityIndicator color={theme.primary} size="large" style={{ marginTop: 48 }} />
        ) : view === "month" ? (
          <MonthView
            days={days}
            anchor={selectedDay}
            events={visibleEvents}
            colorOf={colorOf}
            onPressDay={openDay}
            onPressEvent={setDetailEvent}
            onSwipe={goto}
          />
        ) : view === "schedule" ? (
          <ScheduleView
            rangeStart={range.start}
            rangeEnd={range.end}
            events={visibleEvents}
            colorOf={colorOf}
            loading={loading}
            refreshing={refreshing}
            onRefresh={onRefresh}
            onLoadEarlier={() => setScheduleEarlier((n) => n + 4)}
            onLoadMore={() => setScheduleWeeks((n) => n + 4)}
            onPressEvent={setDetailEvent}
          />
        ) : (
          <TimeGrid
            key={view}
            days={days}
            events={visibleEvents}
            colorOf={colorOf}
            nowTick={nowTick}
            showDayHeader={view !== "day"}
            canCreate={canCreate}
            refreshing={refreshing}
            onRefresh={onRefresh}
            onPressEvent={setDetailEvent}
            onPressSlot={openCreate}
            onPressDay={openDay}
            onSwipe={goto}
          />
        )}

        {pickerOpen ? (
          <MonthPicker
            month={pickerMonth}
            selected={selectedDay}
            eventDays={eventDays}
            onSelect={(day) => {
              setSelectedDay(day);
              setPickerOpen(false);
            }}
            onChangeMonth={(dir) => setPickerMonth((m) => shiftSelected("month", m, dir))}
            onClose={() => setPickerOpen(false)}
          />
        ) : null}
      </View>

      {/* FAB */}
      {canCreate ? (
        <Pressable
          style={[styles.fab, { backgroundColor: theme.primary }]}
          onPress={() => {
            const base = sameDay(selectedDay, today) ? Math.min(23, new Date().getHours() + 1) * 60 : 9 * 60;
            openCreate(selectedDay, base);
          }}
        >
          <Ionicons name="add" size={28} color="#fff" />
        </Pressable>
      ) : null}

      {/* Меню видов */}
      <Modal visible={viewMenuOpen} transparent animationType="fade" onRequestClose={() => setViewMenuOpen(false)}>
        <Pressable style={styles.menuBackdrop} onPress={() => setViewMenuOpen(false)}>
          <View
            style={[
              styles.menu,
              { backgroundColor: theme.surface, borderColor: theme.border, marginTop: insets.top + 48 },
            ]}
          >
            {VIEWS.map((v) => {
              const active = v.key === view;
              return (
                <Pressable
                  key={v.key}
                  onPress={() => changeView(v.key)}
                  style={[styles.menuItem, active && { backgroundColor: theme.surfaceSoft }]}
                >
                  <Ionicons name={v.icon as any} size={20} color={active ? theme.primary : theme.muted} />
                  <Text style={{ color: active ? theme.primary : theme.text, fontSize: 15, fontWeight: active ? "700" : "500" }}>
                    {v.label}
                  </Text>
                </Pressable>
              );
            })}
          </View>
        </Pressable>
      </Modal>

      <EventFormModal
        state={formState}
        statuses={statuses}
        onClose={() => setFormState(null)}
        onDone={() => {
          setFormState(null);
          reload(true);
        }}
      />

      <EventDetailModal
        event={detailEvent}
        statuses={statuses}
        woStates={woStates}
        colorOf={colorOf}
        canEdit={canEdit}
        canDelete={canDelete}
        canCreateWorkOrder={canCreateWo}
        onClose={() => setDetailEvent(null)}
        onChanged={() => {
          setDetailEvent(null);
          reload(true);
        }}
        onEdit={(ev) => {
          setDetailEvent(null);
          setFormState({ mode: "edit", event: ev });
        }}
        onOpenWorkOrder={openWorkOrder}
        onCreateWorkOrder={createWorkOrderFrom}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  header: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 6,
    paddingBottom: 8,
    borderBottomWidth: 1,
    gap: 2,
  },
  headerBtn: { padding: 8 },
  titleBtn: { flex: 1, flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 4 },
  title: { fontSize: 19, fontWeight: "700" },
  todayIcon: {
    width: 22,
    height: 22,
    borderWidth: 2,
    borderRadius: 5,
    alignItems: "center",
    justifyContent: "flex-end",
    paddingBottom: 1,
  },
  weekStrip: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 8,
    paddingVertical: 6,
    borderBottomWidth: 1,
  },
  dayChip: { alignItems: "center", gap: 2, width: 36 },
  dayNum: { width: 32, height: 32, borderRadius: 16, alignItems: "center", justifyContent: "center" },
  dot: { width: 4, height: 4, borderRadius: 2 },
  fab: {
    position: "absolute",
    right: 20,
    bottom: 24,
    width: 56,
    height: 56,
    borderRadius: 16,
    alignItems: "center",
    justifyContent: "center",
    elevation: 6,
    shadowColor: "#000",
    shadowOpacity: 0.3,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 3 },
  },
  menuBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.3)" },
  menu: {
    marginLeft: 10,
    width: 200,
    borderWidth: 1,
    borderRadius: 14,
    paddingVertical: 6,
    elevation: 8,
    shadowColor: "#000",
    shadowOpacity: 0.2,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 6 },
  },
  menuItem: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingVertical: 12 },
});
