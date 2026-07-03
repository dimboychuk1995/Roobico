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

import { Badge, KV, RowCard } from "@/components/ui";
import {
  ApiError,
  CustomerDetails,
  WorkOrderRow,
  fetchCustomerBalance,
  fetchCustomerDetails,
  fetchWorkOrders,
  money,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

export default function CustomerDetailsScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const theme = useTheme();
  const router = useRouter();

  const [customer, setCustomer] = useState<CustomerDetails | null>(null);
  const [balance, setBalance] = useState<number | null>(null);
  const [workOrders, setWorkOrders] = useState<WorkOrderRow[]>([]);
  const [woTotal, setWoTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const [cust, bal, wos] = await Promise.all([
        fetchCustomerDetails(id),
        fetchCustomerBalance(id).catch(() => null),
        fetchWorkOrders("", 1, { customerId: id }).catch(() => null),
      ]);
      setCustomer(cust);
      setBalance(bal);
      setWorkOrders(wos?.items || []);
      setWoTotal(wos?.pagination.total || 0);
      setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load customer.");
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
        <Stack.Screen options={{ title: "Customer" }} />
        <ActivityIndicator color={theme.primary} size="large" />
      </View>
    );
  }

  if (error || !customer) {
    return (
      <View style={[styles.center, { backgroundColor: theme.bg }]}>
        <Stack.Screen options={{ title: "Customer" }} />
        <Text style={{ color: theme.danger, textAlign: "center", padding: 24 }}>
          {error || "Customer not found."}
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
          title: customer.label,
          headerRight: () => (
            <Pressable
              onPress={() => router.push({ pathname: "/customer-form", params: { id: customer.id } })}
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
            {customer.label}
          </Text>
          {!customer.is_active ? <Badge label="Inactive" tone="muted" /> : null}
        </View>
        {balance !== null ? (
          <View style={styles.headerRow}>
            <Text style={{ color: theme.muted, fontSize: 13 }}>Current balance</Text>
            <Text
              style={{
                color: balance > 0 ? theme.warning : theme.primary,
                fontSize: 16,
                fontWeight: "800",
              }}
            >
              {money(balance)}
            </Text>
          </View>
        ) : null}
        <KV label="Address" value={customer.address} />
        <KV label="Taxable" value={customer.taxable ? "Yes" : "No"} />
      </RowCard>

      {customer.contacts.length > 0 ? (
        <>
          <Text style={[styles.sectionTitle, { color: theme.muted }]}>CONTACTS</Text>
          {customer.contacts.map((c, idx) => (
            <RowCard key={idx}>
              <View style={styles.headerRow}>
                <Text style={{ color: theme.text, fontSize: 14, fontWeight: "600" }}>
                  {c.name || "—"}
                </Text>
                {c.is_main ? <Badge label="Main" tone="success" /> : null}
              </View>
              <KV label="Phone" value={c.phone} />
              <KV label="Email" value={c.email} />
            </RowCard>
          ))}
        </>
      ) : null}

      <View style={styles.sectionHeader}>
        <Text style={[styles.sectionTitle, { color: theme.muted }]}>
          UNITS ({customer.units.length})
        </Text>
        <Pressable
          onPress={() => router.push({ pathname: "/unit-form", params: { customerId: customer.id } })}
          hitSlop={8}
        >
          <Text style={{ color: theme.primary, fontWeight: "700", fontSize: 13 }}>+ Add unit</Text>
        </Pressable>
      </View>
      {customer.units.length === 0 ? (
        <RowCard>
          <Text style={{ color: theme.muted, fontSize: 13 }}>No units on file.</Text>
        </RowCard>
      ) : (
        customer.units.map((u) => (
          <Pressable key={u.id} onPress={() => router.push({ pathname: "/unit/[id]", params: { id: u.id } })}>
            <RowCard>
              <View style={styles.headerRow}>
                <Text style={{ color: theme.text, fontSize: 14, fontWeight: "600", flex: 1 }}>
                  {u.label}
                </Text>
                <Ionicons name="chevron-forward" size={16} color={theme.muted} />
              </View>
              <KV label="VIN" value={u.vin} />
              {u.mileage ? <KV label="Mileage" value={String(u.mileage)} /> : null}
            </RowCard>
          </Pressable>
        ))
      )}

      <Text style={[styles.sectionTitle, { color: theme.muted }]}>
        WORK ORDERS ({woTotal})
      </Text>
      {workOrders.length === 0 ? (
        <RowCard>
          <Text style={{ color: theme.muted, fontSize: 13 }}>No work orders yet.</Text>
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
  sectionHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-end",
    marginTop: 14,
    marginBottom: 2,
  },
});
