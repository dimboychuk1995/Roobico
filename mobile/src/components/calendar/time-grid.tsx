/**
 * Сетка времени на 1 / 3 / 7 дней (Day, 3 Day, Week — как в Google
 * Calendar): шапка дней, дорожки механиков внутри каждого дня (как
 * «By mechanic» на вебе — на пустые будущие дни тоже), полоса «не
 * расписанных» WO (аналог all-day), часовая сетка с цветными блоками и
 * бейджем механика, красная линия «сейчас».
 *
 * Ширины считаются в пикселях: если дорожки не влезают, сетка скроллится
 * по горизонтали (шапка и полоса едут синхронно, колонка времени стоит).
 * Тап по пустому слоту в дорожке — новая запись на этого механика, шаг
 * 30 минут; свайп (когда горизонтального скролла нет) — соседний период.
 */
import Ionicons from "@expo/vector-icons/Ionicons";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  NativeScrollEvent,
  NativeSyntheticEvent,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { CalendarEvent } from "@/lib/api";
import { useTheme } from "@/lib/theme";

import {
  DAY_SHORT,
  GUTTER_W,
  HOUR_H,
  Lane,
  Mechanic,
  UNASSIGNED_KEY,
  buildLanes,
  eventLaneKeys,
  eventMechanics,
  eventOnDay,
  eventStart,
  eventTitle,
  fmtTime,
  isCancelled,
  layoutDayEvents,
  mechanicColor,
  mechanicInitials,
  sameDay,
  startOfDay,
  textOn,
  weekdayIndex,
} from "./dates";
import { useSwipe } from "./swipe";

const HEADER_H = 52;
const LANE_ROW_H = 26;
const CHIP_H = 22;
const NAV_W = 28;

interface Props {
  days: Date[];
  events: CalendarEvent[];
  colorOf: (ev: CalendarEvent) => string;
  nowTick: number;
  /** Шапка с днями (3 Day / Week); в Day view её заменяет лента недели. */
  showDayHeader: boolean;
  canCreate: boolean;
  refreshing: boolean;
  /** Механики магазина — для цветов бейджей и дорожек. */
  mechanics: Mechanic[];
  /** Делить каждый день на дорожки по механикам. */
  lanesOn: boolean;
  onRefresh: () => void;
  onPressEvent: (ev: CalendarEvent) => void;
  onPressSlot: (day: Date, minutes: number, lane: Lane | null) => void;
  onPressDay: (day: Date) => void;
  onSwipe: (dir: 1 | -1) => void;
}

export function TimeGrid({
  days,
  events,
  colorOf,
  nowTick,
  showDayHeader,
  canCreate,
  refreshing,
  mechanics,
  lanesOn,
  onRefresh,
  onPressEvent,
  onPressSlot,
  onPressDay,
  onSwipe,
}: Props) {
  const theme = useTheme();
  const [availW, setAvailW] = useState(0);
  const vRef = useRef<ScrollView | null>(null);
  const topRef = useRef<ScrollView | null>(null);
  const bodyRef = useRef<ScrollView | null>(null);
  const didAutoScroll = useRef(false);
  const didHScroll = useRef(false);

  const today = startOfDay(new Date(nowTick));
  const hasToday = days.some((d) => sameDay(d, today));
  const dayCount = days.length;

  const perDay = useMemo(
    () =>
      days.map((day) => {
        const onDay = events.filter((e) => eventOnDay(e, day));
        return {
          day,
          timed: onDay.filter((e) => !e.unscheduled),
          unscheduled: onDay.filter((e) => e.unscheduled && sameDay(eventStart(e), day)),
        };
      }),
    [days, events]
  );

  // Дорожки: все механики магазина + Unassigned, если на видимых днях
  // есть блок без механика.
  const lanes = useMemo<Lane[] | null>(() => {
    if (!lanesOn || !mechanics.length) return null;
    return buildLanes(mechanics, perDay.flatMap((d) => d.timed), theme.muted);
  }, [lanesOn, mechanics, perDay, theme.muted]);
  const laneCount = lanes ? lanes.length : 1;

  // Геометрия в пикселях.
  const laneMin = dayCount === 1 ? 110 : 72;
  const gridW = Math.max(0, availW - GUTTER_W);
  const dayW = gridW ? Math.max(gridW / dayCount, laneCount * laneMin) : 0;
  const laneW = dayW / laneCount;
  const totalW = dayW * dayCount;
  const scrollable = totalW > gridW + 1;

  // Свайп между периодами только пока сетка не скроллится по горизонтали.
  const pan = useSwipe(onSwipe);
  const panHandlers = scrollable ? {} : pan;

  // Раскладка блоков: внутри каждой дорожки своя (пересечения делят дорожку).
  const layouts = useMemo(
    () =>
      perDay.map(({ day, timed }) => {
        if (!lanes) return layoutDayEvents(timed, day).map((p) => ({ ...p, laneIdx: 0 }));
        return lanes.flatMap((lane, li) =>
          layoutDayEvents(
            timed.filter((e) => eventLaneKeys(e, mechanics).includes(lane.key)),
            day
          ).map((p) => ({ ...p, laneIdx: li }))
        );
      }),
    [perDay, lanes, mechanics]
  );

  const anyUnscheduled = perDay.some((d) => d.unscheduled.length > 0);
  const maxChips = dayCount === 1 ? 3 : dayCount === 3 ? 2 : 1;
  const stripH = anyUnscheduled ? 8 + maxChips * CHIP_H : 0;
  const topH = (showDayHeader ? HEADER_H : 0) + (lanes ? LANE_ROW_H : 0) + stripH;

  // Автоскролл к «сейчас − 1ч» (или к 07:00) один раз на маунт.
  useEffect(() => {
    if (didAutoScroll.current || !vRef.current) return;
    didAutoScroll.current = true;
    const hour = hasToday ? Math.max(0, new Date().getHours() - 1) : 7;
    // −12px, чтобы подпись часа (висит над линией) не срезалась кромкой.
    setTimeout(() => vRef.current?.scrollTo({ y: Math.max(0, hour * HOUR_H - 12), animated: false }), 30);
  }, [hasToday]);

  // Горизонтально — к сегодняшнему дню один раз, когда известна ширина.
  useEffect(() => {
    if (!dayW || !scrollable || didHScroll.current) return;
    didHScroll.current = true;
    const idx = Math.max(0, days.findIndex((d) => sameDay(d, today)));
    const x = Math.min(idx * dayW, Math.max(0, totalW - gridW));
    setTimeout(() => {
      bodyRef.current?.scrollTo({ x, animated: false });
      topRef.current?.scrollTo({ x, animated: false });
    }, 30);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dayW, scrollable]);

  const onBodyScroll = (e: NativeSyntheticEvent<NativeScrollEvent>) =>
    topRef.current?.scrollTo({ x: e.nativeEvent.contentOffset.x, animated: false });

  const now = new Date(nowTick);
  const nowTop = ((now.getHours() * 60 + now.getMinutes()) / 60) * HOUR_H;
  const showLaneNames = laneW >= 100;
  const hairline = { borderLeftWidth: StyleSheet.hairlineWidth, borderLeftColor: theme.border };

  const renderChips = (day: Date, unscheduled: CalendarEvent[]) => {
    const overflow = unscheduled.length > maxChips;
    const shown = overflow ? unscheduled.slice(0, maxChips - 1) : unscheduled;
    const rest = unscheduled.length - shown.length;
    const label = (ev: CalendarEvent) =>
      dayW >= 160 ? eventTitle(ev) : ev.wo_number ? `WO #${ev.wo_number}` : "WO";
    return (
      <View key={day.getTime()} style={[styles.unschedCol, { width: dayW }, hairline]}>
        {shown.map((ev) => {
          const color = colorOf(ev);
          return (
            <Pressable
              key={ev.id}
              onPress={() => onPressEvent(ev)}
              style={[styles.chip, { borderColor: color, backgroundColor: theme.surfaceSoft }]}
            >
              <View style={[styles.chipDot, { backgroundColor: color }]} />
              <Text style={{ color: theme.text, fontSize: 11, fontWeight: "600", flexShrink: 1 }} numberOfLines={1}>
                {label(ev)}
              </Text>
            </Pressable>
          );
        })}
        {rest > 0 ? (
          <Pressable onPress={() => onPressDay(day)} style={[styles.chip, { borderColor: theme.border }]}>
            <Text style={{ color: theme.muted, fontSize: 11, fontWeight: "700" }} numberOfLines={1}>
              {shown.length ? `+${rest} more` : `${rest} WO`}
            </Text>
          </Pressable>
        ) : null}
      </View>
    );
  };

  return (
    <View style={{ flex: 1 }} onLayout={(e) => setAvailW(e.nativeEvent.layout.width)} {...panHandlers}>
      {dayW > 0 && topH > 0 ? (
        <View style={[styles.topRow, { backgroundColor: theme.surface, borderBottomColor: theme.border }]}>
          {/* Левый столбик: стрелка назад, пустота под маркеры, подпись полосы WO */}
          <View style={{ width: GUTTER_W }}>
            {showDayHeader ? (
              <Pressable style={[styles.navBtn, { height: HEADER_H }]} onPress={() => onSwipe(-1)} hitSlop={6}>
                <Ionicons name="chevron-back" size={20} color={theme.muted} />
              </Pressable>
            ) : null}
            {lanes ? <View style={{ height: LANE_ROW_H }} /> : null}
            {anyUnscheduled ? (
              <View style={[styles.unschedGutter, { height: stripH }]}>
                <Ionicons name="time-outline" size={13} color={theme.muted} />
                <Text style={{ color: theme.muted, fontSize: 9, fontWeight: "700" }}>WO</Text>
              </View>
            ) : null}
          </View>

          <ScrollView
            horizontal
            ref={topRef}
            scrollEnabled={false}
            showsHorizontalScrollIndicator={false}
            style={{ flex: 1 }}
          >
            <View style={{ width: totalW }}>
              {showDayHeader ? (
                <View style={{ flexDirection: "row", height: HEADER_H }}>
                  {days.map((day) => {
                    const isToday = sameDay(day, today);
                    return (
                      <Pressable
                        key={day.getTime()}
                        style={[styles.headerDay, { width: dayW }, hairline]}
                        onPress={() => onPressDay(day)}
                      >
                        <Text style={{ color: isToday ? theme.primary : theme.muted, fontSize: 11, fontWeight: "600" }}>
                          {DAY_SHORT[weekdayIndex(day)].toUpperCase()}
                        </Text>
                        <View style={[styles.headerNum, isToday && { backgroundColor: theme.primary }]}>
                          <Text style={{ color: isToday ? "#fff" : theme.text, fontSize: 17, fontWeight: isToday ? "800" : "500" }}>
                            {day.getDate()}
                          </Text>
                        </View>
                      </Pressable>
                    );
                  })}
                </View>
              ) : null}

              {lanes ? (
                <View style={{ flexDirection: "row", height: LANE_ROW_H }}>
                  {days.map((day) => (
                    <View key={day.getTime()} style={[{ width: dayW, flexDirection: "row" }, hairline]}>
                      {lanes.map((lane) => {
                        const unassigned = lane.key === UNASSIGNED_KEY;
                        return (
                          <View key={lane.key} style={[styles.laneCell, { width: laneW }]}>
                            <View style={[styles.badge, { backgroundColor: unassigned ? theme.border : lane.color }]}>
                              <Text style={[styles.badgeText, unassigned && { color: theme.muted }]}>
                                {unassigned ? "–" : mechanicInitials(lane.name)}
                              </Text>
                            </View>
                            {showLaneNames ? (
                              <Text
                                style={{ color: unassigned ? theme.muted : theme.text, fontSize: 10.5, fontWeight: "600", flexShrink: 1 }}
                                numberOfLines={1}
                              >
                                {lane.name}
                              </Text>
                            ) : null}
                          </View>
                        );
                      })}
                    </View>
                  ))}
                </View>
              ) : null}

              {anyUnscheduled ? (
                <View style={{ flexDirection: "row", height: stripH }}>
                  {perDay.map(({ day, unscheduled }) => renderChips(day, unscheduled))}
                </View>
              ) : null}
            </View>
          </ScrollView>

          {showDayHeader ? (
            <Pressable
              style={[styles.navRight, { height: HEADER_H, backgroundColor: theme.surface }]}
              onPress={() => onSwipe(1)}
              hitSlop={6}
            >
              <Ionicons name="chevron-forward" size={20} color={theme.muted} />
            </Pressable>
          ) : null}
        </View>
      ) : null}

      <ScrollView
        ref={vRef}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={theme.primary} />}
      >
        <View style={{ height: 24 * HOUR_H + 12, flexDirection: "row" }}>
          {/* Колонка времени — стоит на месте при горизонтальном скролле */}
          <View style={{ width: GUTTER_W }}>
            {Array.from({ length: 24 }, (_, h) => (
              <View key={h} style={{ height: HOUR_H }}>
                {h > 0 ? (
                  <Text style={[styles.hourLabel, { color: theme.muted }]}>{String(h).padStart(2, "0")}:00</Text>
                ) : null}
              </View>
            ))}
          </View>

          {dayW > 0 ? (
            <ScrollView
              horizontal
              ref={bodyRef}
              scrollEnabled={scrollable}
              onScroll={onBodyScroll}
              scrollEventThrottle={16}
              showsHorizontalScrollIndicator={false}
              nestedScrollEnabled
              style={{ flex: 1 }}
            >
              <View style={{ width: totalW, flexDirection: "row" }}>
                {perDay.map(({ day }, di) => {
                  const isToday = sameDay(day, today);
                  return (
                    <View key={day.getTime()} style={[{ width: dayW }, hairline]}>
                      {Array.from({ length: 24 }, (_, h) => (
                        <Pressable
                          key={h}
                          style={[styles.hourSlot, { borderTopColor: theme.border, height: HOUR_H }]}
                          onPress={(e) => {
                            if (!canCreate) return;
                            // locationX/Y — натив; в веб-рендере у MouseEvent есть только offsetX/Y.
                            const ne = e.nativeEvent as { locationX?: number; locationY?: number; offsetX?: number; offsetY?: number };
                            const x = ne.locationX ?? ne.offsetX ?? 0;
                            const y = ne.locationY ?? ne.offsetY ?? 0;
                            const li = Math.min(laneCount - 1, Math.max(0, Math.floor(x / laneW)));
                            onPressSlot(day, h * 60 + (y > HOUR_H / 2 ? 30 : 0), lanes ? lanes[li] : null);
                          }}
                        >
                          <View pointerEvents="none" style={[styles.halfLine, { borderTopColor: theme.border, top: HOUR_H / 2 }]} />
                        </Pressable>
                      ))}

                      {/* Разделители дорожек */}
                      {lanes
                        ? lanes.slice(1).map((lane, i) => (
                            <View
                              key={lane.key}
                              pointerEvents="none"
                              style={[styles.laneSep, { left: (i + 1) * laneW, borderLeftColor: theme.borderStrong }]}
                            />
                          ))
                        : null}

                      {layouts[di].map((p) => {
                        const color = colorOf(p.ev);
                        const fg = textOn(color);
                        const cancelled = isCancelled(p.ev);
                        const blockW = laneW / p.cols - 2;
                        const left = p.laneIdx * laneW + p.col * (laneW / p.cols) + 1;
                        const titleSize = blockW >= 150 ? 12 : blockW >= 90 ? 11 : 9.5;
                        const narrow = blockW < 64;
                        const showTime = p.height >= 36 && blockW >= 90;
                        const showMech = !lanes && p.height >= 54 && blockW >= 150 && !!p.ev.mechanic_name;
                        const mech = eventMechanics(p.ev)[0];
                        const badge = !!mech && blockW >= 80 && p.height >= 30;
                        const titleLines = showTime || narrow ? 1 : Math.max(1, Math.floor(p.height / (titleSize + 4)));
                        const start = eventStart(p.ev);
                        const end = new Date(p.ev.end_time);
                        return (
                          <Pressable
                            key={p.ev.id}
                            onPress={() => onPressEvent(p.ev)}
                            style={[
                              styles.eventBlock,
                              {
                                top: p.top,
                                height: p.height,
                                left,
                                width: Math.max(8, blockW),
                                backgroundColor: color,
                                opacity: cancelled ? 0.55 : 1,
                                borderColor: theme.surface,
                              },
                            ]}
                          >
                            <Text
                              style={[
                                styles.eventTitle,
                                { color: fg, fontSize: titleSize, paddingRight: badge ? 20 : 0 },
                                cancelled && { textDecorationLine: "line-through" },
                              ]}
                              numberOfLines={titleLines}
                              ellipsizeMode={narrow ? "clip" : "tail"}
                            >
                              {p.ev.running ? "▶ " : ""}
                              {eventTitle(p.ev)}
                            </Text>
                            {showTime ? (
                              <Text style={[styles.eventTime, { color: fg }]} numberOfLines={1}>
                                {fmtTime(start)} – {fmtTime(end)}
                                {blockW >= 150 && p.ev.unit_label ? ` · ${p.ev.unit_label}` : ""}
                              </Text>
                            ) : null}
                            {showMech ? (
                              <Text style={[styles.eventTime, { color: fg }]} numberOfLines={1}>
                                {p.ev.mechanic_name}
                              </Text>
                            ) : null}
                            {badge ? (
                              <View style={[styles.badge, styles.eventBadge, { backgroundColor: mechanicColor(mech.id, mechanics) }]}>
                                <Text style={styles.badgeText}>{mechanicInitials(mech.name)}</Text>
                              </View>
                            ) : null}
                          </Pressable>
                        );
                      })}

                      {isToday ? (
                        <View pointerEvents="none" style={[styles.nowLine, { top: nowTop }]}>
                          <View style={styles.nowDot} />
                          <View style={styles.nowBar} />
                        </View>
                      ) : null}
                    </View>
                  );
                })}
              </View>
            </ScrollView>
          ) : null}
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  topRow: { flexDirection: "row", borderBottomWidth: 1 },
  navBtn: { alignItems: "center", justifyContent: "center" },
  navRight: {
    position: "absolute",
    right: 0,
    top: 0,
    width: NAV_W,
    alignItems: "center",
    justifyContent: "center",
    opacity: 0.92,
  },
  headerDay: { alignItems: "center", justifyContent: "center", gap: 2 },
  headerNum: {
    minWidth: 32,
    height: 32,
    borderRadius: 16,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 4,
  },
  laneCell: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 4, paddingHorizontal: 2 },
  badge: {
    minWidth: 18,
    height: 18,
    borderRadius: 9,
    paddingHorizontal: 3,
    alignItems: "center",
    justifyContent: "center",
  },
  badgeText: { color: "#fff", fontSize: 8.5, fontWeight: "800", letterSpacing: 0.3 },
  eventBadge: { position: "absolute", top: 2, right: 2, borderWidth: 1, borderColor: "rgba(255,255,255,0.7)" },
  unschedGutter: { alignItems: "center", justifyContent: "center", gap: 1 },
  unschedCol: { paddingHorizontal: 2, paddingVertical: 4, gap: 2, justifyContent: "flex-start" },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    borderWidth: 1,
    borderRadius: 6,
    paddingHorizontal: 5,
    height: CHIP_H - 2,
  },
  chipDot: { width: 7, height: 7, borderRadius: 4 },
  hourLabel: { fontSize: 11, marginTop: -7, textAlign: "right", paddingRight: 6 },
  hourSlot: { borderTopWidth: StyleSheet.hairlineWidth },
  halfLine: { position: "absolute", left: 0, right: 0, borderTopWidth: StyleSheet.hairlineWidth, opacity: 0.45 },
  laneSep: { position: "absolute", top: 0, bottom: 0, borderLeftWidth: StyleSheet.hairlineWidth, opacity: 0.7 },
  eventBlock: {
    position: "absolute",
    borderRadius: 6,
    paddingHorizontal: 5,
    paddingVertical: 2,
    overflow: "hidden",
    borderWidth: 1,
  },
  eventTitle: { fontWeight: "700" },
  eventTime: { fontSize: 10.5, opacity: 0.9 },
  nowLine: { position: "absolute", left: 0, right: 0, flexDirection: "row", alignItems: "center" },
  nowDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: "#ea4335", marginLeft: -5 },
  nowBar: { flex: 1, height: 2, backgroundColor: "#ea4335" },
});
