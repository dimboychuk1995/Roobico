/**
 * Выпадающий месяц под шапкой (тап по «November 2026 ▾» в Google
 * Calendar): компактная сетка с точками у дней, где есть события.
 * Тап по дню выбирает его и сворачивает панель.
 */
import Ionicons from "@expo/vector-icons/Ionicons";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { useTheme } from "@/lib/theme";

import { DAY_LETTERS, MONTHS, addDays, dayKey, sameDay, sameMonth, startOfDay, startOfMonth, startOfWeek } from "./dates";
import { useSwipe } from "./swipe";

interface Props {
  month: Date;
  selected: Date;
  eventDays: Set<string>;
  onSelect: (day: Date) => void;
  onChangeMonth: (dir: 1 | -1) => void;
  onClose: () => void;
}

export function MonthPicker({ month, selected, eventDays, onSelect, onChangeMonth, onClose }: Props) {
  const theme = useTheme();
  const pan = useSwipe(onChangeMonth);
  const today = startOfDay(new Date());
  const gridStart = startOfWeek(startOfMonth(month));
  const rows = Array.from({ length: 6 }, (_, r) =>
    Array.from({ length: 7 }, (_, c) => addDays(gridStart, r * 7 + c))
  );

  return (
    <View style={StyleSheet.absoluteFill} pointerEvents="box-none">
      <View style={[styles.panel, { backgroundColor: theme.surface, borderBottomColor: theme.border }]} {...pan}>
        <View style={styles.navRow}>
          <Pressable onPress={() => onChangeMonth(-1)} hitSlop={10} style={styles.navBtn}>
            <Ionicons name="chevron-back" size={20} color={theme.text} />
          </Pressable>
          <Text style={{ color: theme.text, fontWeight: "700", fontSize: 15 }}>
            {MONTHS[month.getMonth()]} {month.getFullYear()}
          </Text>
          <Pressable onPress={() => onChangeMonth(1)} hitSlop={10} style={styles.navBtn}>
            <Ionicons name="chevron-forward" size={20} color={theme.text} />
          </Pressable>
        </View>
        <View style={styles.row}>
          {DAY_LETTERS.map((l, i) => (
            <Text key={i} style={[styles.letter, { color: theme.muted }]}>
              {l}
            </Text>
          ))}
        </View>
        {rows.map((row, ri) => (
          <View key={ri} style={styles.row}>
            {row.map((day) => {
              const isSel = sameDay(day, selected);
              const isToday = sameDay(day, today);
              const inMonth = sameMonth(day, month);
              const has = eventDays.has(dayKey(day));
              return (
                <Pressable key={day.getTime()} style={styles.cell} onPress={() => onSelect(day)}>
                  <View
                    style={[
                      styles.num,
                      isSel && { backgroundColor: theme.primary },
                      !isSel && isToday && { borderWidth: 1.5, borderColor: theme.primary },
                    ]}
                  >
                    <Text
                      style={{
                        color: isSel ? "#fff" : isToday ? theme.primary : inMonth ? theme.text : theme.muted,
                        fontWeight: isSel || isToday ? "800" : "500",
                        fontSize: 14,
                        opacity: inMonth || isSel ? 1 : 0.55,
                      }}
                    >
                      {day.getDate()}
                    </Text>
                  </View>
                  <View style={[styles.dot, { backgroundColor: has ? (isSel ? theme.primary : theme.muted) : "transparent" }]} />
                </Pressable>
              );
            })}
          </View>
        ))}
      </View>
      <Pressable style={styles.backdrop} onPress={onClose} />
    </View>
  );
}

const styles = StyleSheet.create({
  panel: {
    borderBottomWidth: 1,
    paddingHorizontal: 8,
    paddingBottom: 8,
    elevation: 8,
    shadowColor: "#000",
    shadowOpacity: 0.18,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 6 },
    zIndex: 2,
  },
  navRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingVertical: 6 },
  navBtn: { padding: 6 },
  row: { flexDirection: "row" },
  letter: { flex: 1, textAlign: "center", fontSize: 11, fontWeight: "700", paddingVertical: 2 },
  cell: { flex: 1, alignItems: "center", paddingVertical: 2 },
  num: { width: 32, height: 32, borderRadius: 16, alignItems: "center", justifyContent: "center" },
  dot: { width: 4, height: 4, borderRadius: 2, marginTop: 1 },
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.25)" },
});
