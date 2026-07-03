import Ionicons from "@expo/vector-icons/Ionicons";
import { Stack, useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";
import { useCallback, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { AttachmentsBlock } from "@/components/attachments-block";
import { Badge, KV, RowCard } from "@/components/ui";
import {
  ApiError,
  UnitDetails,
  WorkOrderRow,
  fetchUnitDetails,
  fetchWorkOrders,
  money,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

export default function UnitDetailsScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const theme = useTheme();
  const router = useRouter();

  const [unit, setUnit] = useState<UnitDetails | null>(null);
  const [workOrders, setWorkOrders] = useState<WorkOrderRow[]>([]);
  const [woTotal, setWoTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const [u, wos] = await Promise.all([
        fetchUnitDetails(id),
        fetchWorkOrders("", 1, { unitId: id }).catch(() => null),
      ]);
      setUnit(u);
      setWorkOrders(wos?.items || []);
      setWoTotal(wos?.pagination.total || 0);
      setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load unit.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [id]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  if (loading) {
    return (
      <View style={[styles.center, { backgroundColor: theme.bg }]}>
        <Stack.Screen options={{ title: "Unit" }} />
        <ActivityIndicator color={theme.primary} size="large" />
      </View>
    );
  }

  if (error || !unit) {
    return (
      <View style={[styles.center, { backgroundColor: theme.bg }]}>
        <Stack.Screen options={{ title: "Unit" }} />
        <Text style={{ color: theme.danger, textAlign: "center", padding: 24 }}>
          {error || "Unit not found."}
        </Text>
      </View>
    );
  }

  return (
    <ScrollView
      style={{ backgroundColor: theme.bg }}
      contentContainerStyle={styles.container}
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
      <Stack.Screen
        options={{
          title: unit.label,
          headerRight: () => (
            <Pressable
              onPress={() => router.push({ pathname: "/unit-form", params: { id: unit.id } })}
              hitSlop={8}
              style={{ paddingHorizontal: 8 }}
            >
              <Ionicons name="create-outline" size={22} color={theme.primary} />
            </Pressable>
          ),
        }}
      />

      <RowCard>
        <View style={styles.headerRow}>
          <Text style={[styles.title, { color: theme.text }]} numberOfLines={2}>
            {unit.label}
          </Text>
          {!unit.is_active ? <Badge label="Inactive" tone="muted" /> : null}
        </View>
        <KV label="Unit #" value={unit.unit_number} />
        <KV label="VIN" value={unit.vin} />
        <KV label="Year" value={unit.year} />
        <KV label="Make" value={unit.make} />
        <KV label="Model" value={unit.model} />
        <KV label="Type" value={unit.type} />
        {unit.mileage ? <KV label="Mileage" value={String(unit.mileage)} /> : null}
      </RowCard>

      {unit.customer_label ? (
        <Pressable onPress={() => router.push({ pathname: "/customer/[id]", params: { id: unit.customer_id } })}>
          <RowCard>
            <KV label="Customer" value={unit.customer_label} />
          </RowCard>
        </Pressable>
      ) : null}

      {unit.annual_inspection ? (
        <>
          <Text style={[styles.sectionTitle, { color: theme.muted }]}>ANNUAL INSPECTION</Text>
          <RowCard>
            <KV label="Date" value={unit.annual_inspection.date} />
            <KV label="Inspector" value={unit.annual_inspection.inspector} />
          </RowCard>
        </>
      ) : null}

      <AttachmentsBlock entityType="unit" entityId={unit.id} />

      <Text style={[styles.sectionTitle, { color: theme.muted }]}>
        WORK ORDERS ({woTotal})
      </Text>
      {workOrders.length === 0 ? (
        <RowCard>
          <Text style={{ color: theme.muted, fontSize: 13 }}>No work orders for this unit.</Text>
        </RowCard>
      ) : (
        workOrders.map((w) => (
          <Pressable key={w.id} onPress={() => router.push({ pathname: "/work-order/[id]", params: { id: w.id } })}>
            <RowCard>
              <View style={styles.headerRow}>
                <Text style={{ color: theme.text, fontSize: 14, fontWeight: "700" }}>
                  WO #{w.wo_number ?? "—"}
                </Text>
                {w.is_paid ? (
                  <Badge label="Paid" tone="success" />
                ) : w.is_in_progress ? (
                  <Badge label="In Progress" tone="info" />
                ) : (
                  <Badge label="Unpaid" tone="warning" />
                )}
              </View>
              <View style={styles.headerRow}>
                <Text style={{ color: theme.muted, fontSize: 13 }}>{w.date}</Text>
                <Text style={{ color: theme.text, fontSize: 14, fontWeight: "700" }}>
                  {money(w.grand_total)}
                </Text>
              </View>
            </RowCard>
          </Pressable>
        ))
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 12, paddingBottom: 40 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 },
  title: { fontSize: 17, fontWeight: "800", flexShrink: 1 },
  sectionTitle: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: 14, marginBottom: 2 },
});
