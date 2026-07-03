import { Stack } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { RowCard } from "@/components/ui";
import { useToast } from "@/context/toast";
import { ApiError, StandardReport, fetchStandardReport } from "@/lib/api";
import { useTheme } from "@/lib/theme";

const REPORTS = [
  { key: "sales_summary", label: "Sales" },
  { key: "payments_summary", label: "Payments" },
  { key: "customer_balances", label: "Customer balances" },
  { key: "vendor_balances", label: "Vendor balances" },
  { key: "parts_orders_summary", label: "Parts orders" },
  { key: "general_revenue", label: "Revenue" },
  { key: "mechanic_hours", label: "Mechanic hours" },
];

const PRESETS = [
  { key: "today", label: "Today" },
  { key: "this_week", label: "Week" },
  { key: "this_month", label: "Month" },
  { key: "this_year", label: "Year" },
  { key: "all_time", label: "All time" },
];

function prettifyKey(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    if (Number.isInteger(value)) return String(value);
    return value.toFixed(2);
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

export default function ReportsScreen() {
  const theme = useTheme();
  const toast = useToast();

  const [reportKey, setReportKey] = useState("sales_summary");
  const [preset, setPreset] = useState("this_month");
  const [report, setReport] = useState<StandardReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await fetchStandardReport(reportKey, preset);
      setReport(data);
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Failed to load report.", "error");
      setReport(null);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reportKey, preset]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  const rows = report?.report_data.rows || [];
  const summary = report?.report_data.summary || {};
  const summaryEntries = Object.entries(summary).filter(
    ([, v]) => typeof v !== "object" || v === null
  );
  const columns = rows.length ? Object.keys(rows[0]).filter((k) => !k.endsWith("_id") && k !== "id") : [];

  return (
    <View style={{ flex: 1, backgroundColor: theme.bg }}>
      <Stack.Screen options={{ title: "Reports" }} />

      {/* Выбор отчёта */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chipScroller} contentContainerStyle={styles.chipRow}>
        {REPORTS.map((r) => {
          const active = r.key === reportKey;
          return (
            <Pressable
              key={r.key}
              onPress={() => setReportKey(r.key)}
              style={[
                styles.chip,
                {
                  backgroundColor: active ? theme.primary : theme.surface,
                  borderColor: active ? theme.primary : theme.border,
                },
              ]}
            >
              <Text style={{ color: active ? "#fff" : theme.text, fontWeight: "600", fontSize: 13 }}>
                {r.label}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>

      {/* Период */}
      <View style={[styles.chipRow, { paddingTop: 0 }]}>
        {PRESETS.map((p) => {
          const active = p.key === preset;
          return (
            <Pressable
              key={p.key}
              onPress={() => setPreset(p.key)}
              style={[
                styles.chipSm,
                {
                  backgroundColor: active ? theme.surfaceSoft : "transparent",
                  borderColor: active ? theme.primary : theme.border,
                },
              ]}
            >
              <Text
                style={{
                  color: active ? theme.primary : theme.muted,
                  fontWeight: "600",
                  fontSize: 12,
                }}
              >
                {p.label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      {loading ? (
        <ActivityIndicator color={theme.primary} size="large" style={{ marginTop: 48 }} />
      ) : (
        <ScrollView
          contentContainerStyle={{ padding: 12, paddingBottom: 40 }}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => {
                setRefreshing(true);
                load();
              }}
              tintColor={theme.primary}
            />
          }
        >
          {report ? (
            <>
              <Text style={[styles.title, { color: theme.text }]}>
                {report.report_data.title || "Report"}
              </Text>

              {summaryEntries.length ? (
                <View style={styles.summaryGrid}>
                  {summaryEntries.map(([k, v]) => (
                    <View
                      key={k}
                      style={[styles.summaryCard, { backgroundColor: theme.surface, borderColor: theme.border }]}
                    >
                      <Text style={{ color: theme.muted, fontSize: 11 }} numberOfLines={1}>
                        {prettifyKey(k)}
                      </Text>
                      <Text style={{ color: theme.text, fontSize: 15, fontWeight: "700" }}>
                        {formatCell(v)}
                      </Text>
                    </View>
                  ))}
                </View>
              ) : null}

              {rows.length === 0 ? (
                <RowCard>
                  <Text style={{ color: theme.muted, fontSize: 13 }}>
                    No data for the selected period.
                  </Text>
                </RowCard>
              ) : (
                <ScrollView horizontal showsHorizontalScrollIndicator>
                  <View>
                    {/* Шапка таблицы */}
                    <View style={[styles.tr, { borderBottomColor: theme.border }]}>
                      {columns.map((c) => (
                        <Text
                          key={c}
                          style={[styles.th, { color: theme.muted }]}
                          numberOfLines={1}
                        >
                          {prettifyKey(c)}
                        </Text>
                      ))}
                    </View>
                    {rows.map((row, i) => (
                      <View
                        key={i}
                        style={[
                          styles.tr,
                          {
                            borderBottomColor: theme.border,
                            backgroundColor: i % 2 ? theme.surfaceSoft : "transparent",
                          },
                        ]}
                      >
                        {columns.map((c) => (
                          <Text key={c} style={[styles.td, { color: theme.text }]} numberOfLines={1}>
                            {formatCell(row[c])}
                          </Text>
                        ))}
                      </View>
                    ))}
                  </View>
                </ScrollView>
              )}
            </>
          ) : (
            <RowCard>
              <Text style={{ color: theme.muted, fontSize: 13 }}>Report unavailable.</Text>
            </RowCard>
          )}
        </ScrollView>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  chipScroller: { flexGrow: 0 },
  chipRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
    alignItems: "center",
  },
  chip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 13, paddingVertical: 6 },
  chipSm: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 4 },
  title: { fontSize: 17, fontWeight: "800", marginBottom: 8 },
  summaryGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 12 },
  summaryCard: {
    borderWidth: 1,
    borderRadius: 12,
    padding: 10,
    flexBasis: "31%",
    flexGrow: 1,
  },
  tr: { flexDirection: "row", borderBottomWidth: 1, paddingVertical: 8 },
  th: { width: 120, fontSize: 11, fontWeight: "800", paddingHorizontal: 6, textTransform: "uppercase" },
  td: { width: 120, fontSize: 13, paddingHorizontal: 6 },
});
