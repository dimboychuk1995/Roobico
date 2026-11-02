/**
 * Месяц как в Google Calendar на телефоне: сетка 6×7 на весь экран,
 * в ячейке — номер дня и цветные чипы событий (сколько влезает, остальное
 * «+N»). Тап по дню открывает Day view, тап по чипу — деталку события.
 */
import { useMemo, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { CalendarEvent } from "@/lib/api";
import { useTheme } from "@/lib/theme";

import { DAY_LETTERS, dayKey, eventTitle, groupByDay, isCancelled, sameDay, sameMonth, startOfDay, textOn } from "./dates";
import { useSwipe } from "./swipe";

interface Props {
  days: Date[]; // 42
  anchor: Date;
  events: CalendarEvent[];
  colorOf: (ev: CalendarEvent) => string;
  onPressDay: (day: Date) => void;
  onPressEvent: (ev: CalendarEvent) => void;
  onSwipe: (dir: 1 | -1) => void;
}

const CHIP_H = 15;
const NUM_H = 24;

export function MonthView({ days, anchor, events, colorOf, onPressDay, onPressEvent, onSwipe }: Props) {
  const theme = useTheme();
  const pan = useSwipe(onSwipe);
  const [gridH, setGridH] = useState(0);
  const today = startOfDay(new Date());
  const byDay = useMemo(() => groupByDay(events), [events]);

  const cellH = gridH ? gridH / 6 : 0;
  const fit = cellH ? Math.max(1, Math.floor((cellH - NUM_H - 2) / CHIP_H)) : 2;

  const rows = Array.from({ length: 6 }, (_, r) => days.slice(r * 7, r * 7 + 7));

  return (
    <View style={{ flex: 1 }} {...pan}>
      <View style={[styles.lettersRow, { borderBottomColor: theme.border, backgroundColor: theme.surface }]}>
        {DAY_LETTERS.map((l, i) => (
          <Text key={i} style={[styles.letter, { color: theme.muted }]}>
            {l}
          </Text>
        ))}
      </View>
      <View style={{ flex: 1 }} onLayout={(e) => setGridH(e.nativeEvent.layout.height)}>
        {rows.map((row, ri) => (
          <View key={ri} style={{ flex: 1, flexDirection: "row" }}>
            {row.map((day) => {
              const inMonth = sameMonth(day, anchor);
              const isToday = sameDay(day, today);
              const list = byDay.get(dayKey(day)) || [];
              // Если всё не влезает — последняя строка уходит под «+N».
              const shown = list.length > fit ? list.slice(0, Math.max(0, fit - 1)) : list;
              const rest = list.length - shown.length;
              return (
                <Pressable
                  key={day.getTime()}
                  onPress={() => onPressDay(day)}
                  style={[styles.cell, { borderColor: theme.border, opacity: inMonth ? 1 : 0.5 }]}
                >
                  <View style={[styles.num, isToday && { backgroundColor: theme.primary }]}>
                    <Text
                      style={{
                        color: isToday ? "#fff" : theme.text,
                        fontSize: 12,
                        fontWeight: isToday ? "800" : "500",
                      }}
                    >
                      {day.getDate()}
                    </Text>
                  </View>
                  {shown.map((ev) => {
                    const color = colorOf(ev);
                    const cancelled = isCancelled(ev);
                    return (
                      <Pressable
                        key={ev.id}
                        onPress={() => onPressEvent(ev)}
                        style={[
                          styles.chip,
                          ev.unscheduled
                            ? { borderColor: color, borderWidth: 1, backgroundColor: "transparent" }
                            : { backgroundColor: color },
                          cancelled && { opacity: 0.55 },
                        ]}
                      >
                        <Text
                          style={[
                            styles.chipText,
                            { color: ev.unscheduled ? theme.text : textOn(color) },
                            cancelled && { textDecorationLine: "line-through" },
                          ]}
                          numberOfLines={1}
                        >
                          {eventTitle(ev)}
                        </Text>
                      </Pressable>
                    );
                  })}
                  {rest > 0 ? (
                    <Text style={[styles.more, { color: theme.muted }]} numberOfLines={1}>
                      +{rest} more
                    </Text>
                  ) : null}
                </Pressable>
              );
            })}
          </View>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  lettersRow: { flexDirection: "row", paddingVertical: 4, borderBottomWidth: 1 },
  letter: { flex: 1, textAlign: "center", fontSize: 11, fontWeight: "700" },
  cell: {
    flex: 1,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderLeftWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 2,
    paddingBottom: 1,
    overflow: "hidden",
  },
  num: {
    alignSelf: "center",
    width: 22,
    height: 22,
    borderRadius: 11,
    alignItems: "center",
    justifyContent: "center",
    marginTop: 1,
    marginBottom: 1,
  },
  chip: { borderRadius: 3, paddingHorizontal: 3, height: CHIP_H - 2, marginBottom: 2, justifyContent: "center" },
  chipText: { fontSize: 9, fontWeight: "600", lineHeight: 11 },
  more: { fontSize: 9, fontWeight: "700", paddingHorizontal: 2 },
});
