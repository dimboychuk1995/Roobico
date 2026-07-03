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
import { useAuth } from "@/context/auth";
import { ApiError, DashboardMetrics, fetchDashboardMetrics, money } from "@/lib/api";
import { useTheme } from "@/lib/theme";

const PRESETS = [
  { key: "today", label: "Today" },
  { key: "this_week", label: "Week" },
  { key: "this_month", label: "Month" },
  { key: "this_year", label: "Year" },
];

export default function DashboardScreen() {
  const theme = useTheme();
  const { session } = useAuth();

  const [preset, setPreset] = useState("this_month");
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (p: string) => {
    try {
      const data = await fetchDashboardMetrics(p);
      setMetrics(data);
      setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load metrics.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    load(preset);
  }, [preset, load]);

  const activeShop = session?.shops.find((s) => s.id === session.active_shop_id);

  return (
    <ScrollView
      style={{ backgroundColor: theme.bg }}
      contentContainerStyle={styles.container}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={() => {
            setRefreshing(true);
            load(preset);
          }}
          tintColor={theme.primary}
        />
      }
    >
      {activeShop ? (
        <Text style={[styles.shopName, { color: theme.muted }]}>{activeShop.name}</Text>
      ) : null}

      <View style={styles.presetRow}>
        {PRESETS.map((p) => {
          const active = p.key === preset;
          return (
            <Pressable
              key={p.key}
              onPress={() => setPreset(p.key)}
              style={[
                styles.presetChip,
                {
                  backgroundColor: active ? theme.primary : theme.surface,
                  borderColor: active ? theme.primary : theme.border,
                },
              ]}
            >
              <Text style={{ color: active ? "#fff" : theme.text, fontWeight: "600", fontSize: 13 }}>
                {p.label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      {loading ? (
        <ActivityIndicator color={theme.primary} size="large" style={{ marginTop: 48 }} />
      ) : error ? (
        <Text style={{ color: theme.danger, marginTop: 24, textAlign: "center" }}>{error}</Text>
      ) : metrics ? (
        <>
          <RowCard>
            <Text style={[styles.cardLabel, { color: theme.muted }]}>OUTSTANDING BALANCE</Text>
            <Text style={[styles.bigNumber, { color: theme.primary }]}>
              {money(metrics.outstanding_balance)}
            </Text>
          </RowCard>

          <View style={styles.grid}>
            <Stat label="Revenue (period)" value={money(metrics.period_money_total)} />
            <Stat label="Paid" value={money(metrics.period_paid_amount)} />
            <Stat label="Unpaid" value={money(metrics.period_unpaid_amount)} />
            <Stat label="Work orders" value={String(metrics.period_total)} />
            <Stat label="Labor" value={money(metrics.period_labor_total)} />
            <Stat label="Parts" value={money(metrics.period_parts_total)} />
          </View>

          <RowCard>
            <Text style={[styles.cardLabel, { color: theme.muted }]}>PARTS ORDERS (PERIOD)</Text>
            <View style={styles.inlineStats}>
              <Stat label="Total" value={String(metrics.period_parts_orders_total ?? 0)} bare />
              <Stat label="Received" value={String(metrics.period_parts_orders_received ?? 0)} bare />
              <Stat label="Paid" value={money(metrics.period_parts_orders_paid_amount)} bare />
              <Stat label="Unpaid" value={money(metrics.period_parts_orders_unpaid_amount)} bare />
            </View>
          </RowCard>
        </>
      ) : null}
    </ScrollView>
  );
}

function Stat({ label, value, bare }: { label: string; value: string; bare?: boolean }) {
  const theme = useTheme();
  const inner = (
    <>
      <Text style={[styles.statLabel, { color: theme.muted }]}>{label}</Text>
      <Text style={[styles.statValue, { color: theme.text }]}>{value}</Text>
    </>
  );
  if (bare) return <View style={styles.bareStat}>{inner}</View>;
  return (
    <View
      style={[styles.statCard, { backgroundColor: theme.surface, borderColor: theme.border }]}
    >
      {inner}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { padding: 12, paddingBottom: 32 },
  shopName: { fontSize: 13, marginBottom: 8 },
  presetRow: { flexDirection: "row", gap: 8, marginBottom: 4 },
  presetChip: {
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 6,
  },
  cardLabel: { fontSize: 11, fontWeight: "700", letterSpacing: 1 },
  bigNumber: { fontSize: 32, fontWeight: "800", marginTop: 2 },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 8 },
  statCard: {
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
    flexBasis: "31%",
    flexGrow: 1,
  },
  bareStat: { flexGrow: 1 },
  inlineStats: { flexDirection: "row", flexWrap: "wrap", gap: 12, marginTop: 6 },
  statLabel: { fontSize: 12 },
  statValue: { fontSize: 16, fontWeight: "700", marginTop: 2 },
});
