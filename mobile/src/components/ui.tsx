/**
 * Мелкие общие элементы: карточка строки списка, бэдж статуса,
 * строка "лейбл-значение".
 */
import React from "react";
import { StyleSheet, Text, View } from "react-native";

import { useTheme } from "@/lib/theme";

export function RowCard({ children }: { children: React.ReactNode }) {
  const theme = useTheme();
  return (
    <View
      style={[
        styles.card,
        { backgroundColor: theme.surface, borderColor: theme.border },
      ]}
    >
      {children}
    </View>
  );
}

export function Badge({
  label,
  tone,
}: {
  label: string;
  tone: "success" | "warning" | "muted" | "danger" | "info";
}) {
  const theme = useTheme();
  const tones = {
    success: { bg: "rgba(34,197,94,0.16)", fg: theme.primaryDeep },
    warning: { bg: "rgba(245,158,11,0.18)", fg: "#b45309" },
    muted: { bg: "rgba(148,163,184,0.18)", fg: theme.muted },
    danger: { bg: "rgba(239,68,68,0.16)", fg: theme.danger },
    info: { bg: "rgba(56,189,248,0.18)", fg: "#0e7490" },
  } as const;
  const t = tones[tone];
  return (
    <View style={[styles.badge, { backgroundColor: t.bg }]}>
      <Text style={[styles.badgeText, { color: t.fg }]}>{label}</Text>
    </View>
  );
}

export function KV({ label, value }: { label: string; value: string }) {
  const theme = useTheme();
  if (!value) return null;
  return (
    <View style={styles.kvRow}>
      <Text style={[styles.kvLabel, { color: theme.muted }]}>{label}</Text>
      <Text style={[styles.kvValue, { color: theme.text }]} numberOfLines={1}>
        {value}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
    marginTop: 8,
    gap: 4,
  },
  badge: {
    borderRadius: 999,
    paddingHorizontal: 8,
    paddingVertical: 2,
    alignSelf: "flex-start",
  },
  badgeText: { fontSize: 12, fontWeight: "700" },
  kvRow: { flexDirection: "row", justifyContent: "space-between", gap: 12 },
  kvLabel: { fontSize: 13 },
  kvValue: { fontSize: 13, fontWeight: "500", flexShrink: 1 },
});
