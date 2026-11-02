/**
 * Schedule (agenda) как в Google Calendar: лента дней с событиями,
 * пустые дни пропускаются, при смене месяца — полоса с названием месяца.
 * Начинается с выбранного дня; «Show earlier / Show more» расширяют диапазон.
 */
import Ionicons from "@expo/vector-icons/Ionicons";
import { useMemo } from "react";
import { ActivityIndicator, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";

import { CalendarEvent } from "@/lib/api";
import { useTheme } from "@/lib/theme";

import {
  DAY_SHORT,
  MONTHS,
  dayKey,
  eventEnd,
  eventStart,
  eventTitle,
  fmtDuration,
  fmtTime,
  groupByDay,
  isCancelled,
  sameDay,
  startOfDay,
  textOn,
  weekdayIndex,
} from "./dates";

interface Props {
  rangeStart: Date;
  rangeEnd: Date;
  events: CalendarEvent[];
  colorOf: (ev: CalendarEvent) => string;
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  onLoadEarlier: () => void;
  onLoadMore: () => void;
  onPressEvent: (ev: CalendarEvent) => void;
}

type Row =
  | { type: "month"; key: string; label: string }
  | { type: "day"; key: string; day: Date; events: CalendarEvent[] };

export function ScheduleView({
  rangeStart,
  rangeEnd,
  events,
  colorOf,
  loading,
  refreshing,
  onRefresh,
  onLoadEarlier,
  onLoadMore,
  onPressEvent,
}: Props) {
  const theme = useTheme();
  const today = startOfDay(new Date());

  const rows = useMemo<Row[]>(() => {
    const byDay = groupByDay(
      events.filter((e) => {
        const t = eventStart(e).getTime();
        return t >= rangeStart.getTime() && t < rangeEnd.getTime();
      })
    );
    const keys = Array.from(byDay.keys()).sort();
    const out: Row[] = [];
    let lastMonth = "";
    for (const k of keys) {
      const list = byDay.get(k)!;
      const day = startOfDay(eventStart(list[0]));
      const m = `${day.getFullYear()}-${day.getMonth()}`;
      if (m !== lastMonth) {
        lastMonth = m;
        out.push({ type: "month", key: `m:${m}`, label: `${MONTHS[day.getMonth()]} ${day.getFullYear()}` });
      }
      out.push({ type: "day", key: k, day, events: list });
    }
    return out;
  }, [events, rangeStart, rangeEnd]);

  const header = (
    <Pressable style={styles.moreBtn} onPress={onLoadEarlier}>
      <Ionicons name="chevron-up" size={16} color={theme.primary} />
      <Text style={{ color: theme.primary, fontWeight: "700", fontSize: 13 }}>Show earlier</Text>
    </Pressable>
  );
  const footer = (
    <View style={{ paddingBottom: 90 }}>
      {loading ? <ActivityIndicator color={theme.primary} style={{ marginVertical: 12 }} /> : null}
      {!loading && rows.length === 0 ? (
        <View style={styles.empty}>
          <Ionicons name="calendar-outline" size={36} color={theme.muted} />
          <Text style={{ color: theme.muted, marginTop: 8, fontSize: 14 }}>
            Nothing scheduled from {DAY_SHORT[weekdayIndex(rangeStart)]}, {MONTHS[rangeStart.getMonth()].slice(0, 3)}{" "}
            {rangeStart.getDate()}
          </Text>
        </View>
      ) : null}
      <Pressable style={styles.moreBtn} onPress={onLoadMore}>
        <Text style={{ color: theme.primary, fontWeight: "700", fontSize: 13 }}>Show more</Text>
        <Ionicons name="chevron-down" size={16} color={theme.primary} />
      </Pressable>
    </View>
  );

  return (
    <FlatList
      data={rows}
      keyExtractor={(r) => r.key}
      ListHeaderComponent={header}
      ListFooterComponent={footer}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={theme.primary} />}
      contentContainerStyle={{ paddingHorizontal: 10 }}
      renderItem={({ item }) => {
        if (item.type === "month") {
          return (
            <View style={[styles.monthBand, { backgroundColor: theme.primary }]}>
              <Text style={styles.monthBandText}>{item.label}</Text>
            </View>
          );
        }
        const isToday = sameDay(item.day, today);
        return (
          <View style={styles.dayRow}>
            <View style={styles.dayCol}>
              <Text style={{ color: isToday ? theme.primary : theme.muted, fontSize: 10, fontWeight: "700" }}>
                {DAY_SHORT[weekdayIndex(item.day)].toUpperCase()}
              </Text>
              <View style={[styles.dayNum, isToday && { backgroundColor: theme.primary }]}>
                <Text style={{ color: isToday ? "#fff" : theme.text, fontSize: 17, fontWeight: isToday ? "800" : "500" }}>
                  {item.day.getDate()}
                </Text>
              </View>
            </View>
            <View style={{ flex: 1, gap: 4 }}>
              {item.events.map((ev) => {
                const color = colorOf(ev);
                const fg = textOn(color);
                const cancelled = isCancelled(ev);
                const worked = (ev.worked || []).filter((w) => w.seconds > 0 || w.running);
                const meta = [
                  ev.unscheduled ? "Not scheduled" : `${fmtTime(eventStart(ev))} – ${fmtTime(eventEnd(ev))}`,
                  ev.unit_label,
                  ev.mechanic_name,
                ]
                  .filter(Boolean)
                  .join(" · ");
                return (
                  <Pressable
                    key={ev.id}
                    onPress={() => onPressEvent(ev)}
                    style={[
                      styles.pill,
                      ev.unscheduled
                        ? { backgroundColor: theme.surface, borderColor: color, borderWidth: 1.5 }
                        : { backgroundColor: color },
                      cancelled && { opacity: 0.55 },
                    ]}
                  >
                    <Text
                      style={[
                        styles.pillTitle,
                        { color: ev.unscheduled ? theme.text : fg },
                        cancelled && { textDecorationLine: "line-through" },
                      ]}
                      numberOfLines={1}
                    >
                      {ev.running ? "▶ " : ""}
                      {eventTitle(ev)}
                    </Text>
                    <Text style={[styles.pillMeta, { color: ev.unscheduled ? theme.muted : fg }]} numberOfLines={1}>
                      {meta}
                    </Text>
                    {worked.length ? (
                      <Text style={[styles.pillMeta, { color: ev.unscheduled ? theme.muted : fg }]} numberOfLines={1}>
                        {worked.map((w) => `${w.name} ${fmtDuration(w.seconds)}${w.running ? " ▶" : ""}`).join(", ")}
                      </Text>
                    ) : null}
                  </Pressable>
                );
              })}
            </View>
          </View>
        );
      }}
      // Ключ дня в rows меняется при смене диапазона — сбрасываем позицию.
      key={dayKey(rangeStart)}
    />
  );
}

const styles = StyleSheet.create({
  moreBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 4,
    paddingVertical: 12,
  },
  monthBand: { borderRadius: 10, paddingVertical: 10, paddingHorizontal: 14, marginVertical: 6 },
  monthBandText: { color: "#fff", fontSize: 15, fontWeight: "800", letterSpacing: 0.5 },
  dayRow: { flexDirection: "row", gap: 8, paddingVertical: 6 },
  dayCol: { width: 44, alignItems: "center", gap: 2 },
  dayNum: { width: 32, height: 32, borderRadius: 16, alignItems: "center", justifyContent: "center" },
  pill: { borderRadius: 8, paddingHorizontal: 10, paddingVertical: 7 },
  pillTitle: { fontSize: 13.5, fontWeight: "700" },
  pillMeta: { fontSize: 11.5, marginTop: 1, opacity: 0.92 },
  empty: { alignItems: "center", paddingVertical: 36 },
});
