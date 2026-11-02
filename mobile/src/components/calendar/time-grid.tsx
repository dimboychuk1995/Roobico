/**
 * Сетка времени на 1 / 3 / 7 дней (Day, 3 Day, Week — как в Google
 * Calendar): шапка дней, полоса «не расписанных» WO (аналог all-day),
 * часовая сетка с цветными блоками, красная линия «сейчас». Тап по пустому
 * слоту — создание записи с шагом 30 минут, свайп — соседний период.
 */
import Ionicons from "@expo/vector-icons/Ionicons";
import { useEffect, useMemo, useRef } from "react";
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";

import { CalendarEvent } from "@/lib/api";
import { useTheme } from "@/lib/theme";

import {
  DAY_SHORT,
  GUTTER_W,
  HOUR_H,
  eventOnDay,
  eventStart,
  eventTitle,
  fmtTime,
  isCancelled,
  layoutDayEvents,
  sameDay,
  startOfDay,
  textOn,
  weekdayIndex,
} from "./dates";
import { useSwipe } from "./swipe";

interface Props {
  days: Date[];
  events: CalendarEvent[];
  colorOf: (ev: CalendarEvent) => string;
  nowTick: number;
  /** Шапка с днями (3 Day / Week); в Day view её заменяет лента недели. */
  showDayHeader: boolean;
  canCreate: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  onPressEvent: (ev: CalendarEvent) => void;
  onPressSlot: (day: Date, minutes: number) => void;
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
  onRefresh,
  onPressEvent,
  onPressSlot,
  onPressDay,
  onSwipe,
}: Props) {
  const theme = useTheme();
  const pan = useSwipe(onSwipe);
  const scrollRef = useRef<ScrollView | null>(null);
  const didAutoScroll = useRef(false);

  const today = startOfDay(new Date(nowTick));
  const hasToday = days.some((d) => sameDay(d, today));
  const dayCount = days.length;

  const perDay = useMemo(
    () =>
      days.map((day) => {
        const onDay = events.filter((e) => eventOnDay(e, day));
        return {
          day,
          timed: layoutDayEvents(onDay.filter((e) => !e.unscheduled), day),
          unscheduled: onDay.filter((e) => e.unscheduled && sameDay(eventStart(e), day)),
        };
      }),
    [days, events]
  );
  const anyUnscheduled = perDay.some((d) => d.unscheduled.length > 0);
  const maxChips = dayCount === 1 ? 4 : dayCount === 3 ? 2 : 1;

  // Автоскролл к «сейчас − 1ч» (или к 07:00) один раз на маунт.
  useEffect(() => {
    if (didAutoScroll.current || !scrollRef.current) return;
    didAutoScroll.current = true;
    const hour = hasToday ? Math.max(0, new Date().getHours() - 1) : 7;
    // −12px, чтобы подпись часа (висит над линией) не срезалась кромкой.
    setTimeout(() => scrollRef.current?.scrollTo({ y: Math.max(0, hour * HOUR_H - 12), animated: false }), 30);
  }, [hasToday]);

  const now = new Date(nowTick);
  const nowTop = ((now.getHours() * 60 + now.getMinutes()) / 60) * HOUR_H;

  const titleSize = dayCount === 1 ? 12 : dayCount === 3 ? 11 : 9.5;

  return (
    <View style={{ flex: 1 }} {...pan}>
      {showDayHeader ? (
        <View style={[styles.headerRow, { borderBottomColor: theme.border, backgroundColor: theme.surface }]}>
          <View style={{ width: GUTTER_W }} />
          {days.map((day) => {
            const isToday = sameDay(day, today);
            return (
              <Pressable key={day.getTime()} style={styles.headerDay} onPress={() => onPressDay(day)}>
                <Text style={{ color: isToday ? theme.primary : theme.muted, fontSize: 11, fontWeight: "600" }}>
                  {DAY_SHORT[weekdayIndex(day)].toUpperCase()}
                </Text>
                <View style={[styles.headerNum, isToday && { backgroundColor: theme.primary }]}>
                  <Text
                    style={{
                      color: isToday ? "#fff" : theme.text,
                      fontSize: dayCount === 7 ? 15 : 18,
                      fontWeight: isToday ? "800" : "500",
                    }}
                  >
                    {day.getDate()}
                  </Text>
                </View>
              </Pressable>
            );
          })}
        </View>
      ) : null}

      {anyUnscheduled ? (
        <View style={[styles.unschedRow, { borderBottomColor: theme.border, backgroundColor: theme.surface }]}>
          <View style={[styles.unschedGutter, { width: GUTTER_W }]}>
            <Ionicons name="time-outline" size={13} color={theme.muted} />
            <Text style={{ color: theme.muted, fontSize: 9, fontWeight: "700" }}>WO</Text>
          </View>
          {perDay.map(({ day, unscheduled }) => {
            const shown = unscheduled.slice(0, maxChips);
            const rest = unscheduled.length - shown.length;
            return (
              <View key={day.getTime()} style={[styles.unschedCol, { borderLeftColor: theme.border }]}>
                {shown.map((ev) => {
                  const color = colorOf(ev);
                  return (
                    <Pressable
                      key={ev.id}
                      onPress={() => onPressEvent(ev)}
                      style={[styles.chip, { borderColor: color, backgroundColor: theme.surfaceSoft }]}
                    >
                      <View style={[styles.chipDot, { backgroundColor: color }]} />
                      <Text style={{ color: theme.text, fontSize: dayCount === 7 ? 9 : 11, fontWeight: "600", flex: 1 }} numberOfLines={1}>
                        {eventTitle(ev)}
                      </Text>
                    </Pressable>
                  );
                })}
                {rest > 0 ? (
                  <Pressable onPress={() => onPressDay(day)} hitSlop={4}>
                    <Text style={{ color: theme.muted, fontSize: 10, fontWeight: "700", paddingHorizontal: 4 }}>
                      +{rest} more
                    </Text>
                  </Pressable>
                ) : null}
              </View>
            );
          })}
        </View>
      ) : null}

      <ScrollView
        ref={scrollRef}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={theme.primary} />}
      >
        <View style={{ height: 24 * HOUR_H + 12, flexDirection: "row" }}>
          {/* Левая колонка времени */}
          <View style={{ width: GUTTER_W }}>
            {Array.from({ length: 24 }, (_, h) => (
              <View key={h} style={{ height: HOUR_H }}>
                {h > 0 ? (
                  <Text style={[styles.hourLabel, { color: theme.muted }]}>{String(h).padStart(2, "0")}:00</Text>
                ) : null}
              </View>
            ))}
          </View>

          {perDay.map(({ day, timed }) => {
            const isToday = sameDay(day, today);
            return (
              <View key={day.getTime()} style={[styles.dayCol, { borderLeftColor: theme.border }]}>
                {Array.from({ length: 24 }, (_, h) => (
                  <Pressable
                    key={h}
                    style={[styles.hourSlot, { borderTopColor: theme.border, height: HOUR_H }]}
                    onPress={(e) => {
                      if (!canCreate) return;
                      const y = e.nativeEvent.locationY || 0;
                      onPressSlot(day, h * 60 + (y > HOUR_H / 2 ? 30 : 0));
                    }}
                  >
                    <View pointerEvents="none" style={[styles.halfLine, { borderTopColor: theme.border, top: HOUR_H / 2 }]} />
                  </Pressable>
                ))}

                {timed.map((p) => {
                  const color = colorOf(p.ev);
                  const fg = textOn(color);
                  const widthPct = 100 / p.cols;
                  const cancelled = isCancelled(p.ev);
                  const start = eventStart(p.ev);
                  const end = new Date(p.ev.end_time);
                  const showTime = p.height >= 36 && dayCount < 7;
                  // Узкая колонка (пересечения в 3 Day / Week) — одна строка с
                  // обрезкой, иначе RN ломает слово по буквам в столбик.
                  const narrow = dayCount > 1 && p.cols > 1;
                  const titleLines = showTime || narrow ? 1 : Math.max(1, Math.floor(p.height / (titleSize + 4)));
                  return (
                    <Pressable
                      key={p.ev.id}
                      onPress={() => onPressEvent(p.ev)}
                      style={[
                        styles.eventBlock,
                        {
                          top: p.top,
                          height: p.height,
                          left: `${p.col * widthPct}%`,
                          width: `${widthPct}%`,
                          backgroundColor: color,
                          opacity: cancelled ? 0.55 : 1,
                          borderColor: theme.surface,
                        },
                      ]}
                    >
                      <Text
                        style={[
                          styles.eventTitle,
                          { color: fg, fontSize: titleSize },
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
                          {dayCount === 1 && p.ev.unit_label ? ` · ${p.ev.unit_label}` : ""}
                        </Text>
                      ) : null}
                      {dayCount === 1 && p.height >= 54 && p.ev.mechanic_name ? (
                        <Text style={[styles.eventTime, { color: fg }]} numberOfLines={1}>
                          {p.ev.mechanic_name}
                        </Text>
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
    </View>
  );
}

const styles = StyleSheet.create({
  headerRow: { flexDirection: "row", paddingVertical: 6, borderBottomWidth: 1 },
  headerDay: { flex: 1, alignItems: "center", gap: 2 },
  headerNum: {
    minWidth: 34,
    height: 34,
    borderRadius: 17,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 4,
  },
  unschedRow: { flexDirection: "row", borderBottomWidth: 1, paddingVertical: 4, minHeight: 30 },
  unschedGutter: { alignItems: "center", justifyContent: "center", gap: 1 },
  unschedCol: { flex: 1, borderLeftWidth: StyleSheet.hairlineWidth, paddingHorizontal: 2, gap: 2, justifyContent: "center" },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    borderWidth: 1,
    borderRadius: 6,
    paddingHorizontal: 5,
    paddingVertical: 3,
  },
  chipDot: { width: 7, height: 7, borderRadius: 4 },
  hourLabel: { fontSize: 11, marginTop: -7, textAlign: "right", paddingRight: 6 },
  dayCol: { flex: 1, borderLeftWidth: StyleSheet.hairlineWidth },
  hourSlot: { borderTopWidth: StyleSheet.hairlineWidth },
  halfLine: { position: "absolute", left: 0, right: 0, borderTopWidth: StyleSheet.hairlineWidth, opacity: 0.45 },
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
