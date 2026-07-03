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
  VendorFull,
  VendorOrderRow,
  VendorOrdersSummary,
  fetchVendorFull,
  fetchVendorOrders,
  money,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

export default function VendorDetailsScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const theme = useTheme();
  const router = useRouter();

  const [vendor, setVendor] = useState<VendorFull | null>(null);
  const [orders, setOrders] = useState<VendorOrderRow[]>([]);
  const [summary, setSummary] = useState<VendorOrdersSummary | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const [v, o] = await Promise.all([
        fetchVendorFull(id),
        fetchVendorOrders(id, 1).catch(() => null),
      ]);
      setVendor(v);
      setOrders(o?.items || []);
      setSummary(o?.summary || null);
      setHasMore(!!o?.pagination?.has_next);
      setPage(1);
      setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load vendor.");
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

  const loadMore = async () => {
    if (!id || loadingMore || !hasMore) return;
    setLoadingMore(true);
    try {
      const o = await fetchVendorOrders(id, page + 1);
      setOrders((prev) => [...prev, ...o.items]);
      setHasMore(!!o.pagination?.has_next);
      setPage(page + 1);
    } catch {
      // молча — кнопка останется
    } finally {
      setLoadingMore(false);
    }
  };

  if (loading) {
    return (
      <View style={[styles.center, { backgroundColor: theme.bg }]}>
        <Stack.Screen options={{ title: "Vendor" }} />
        <ActivityIndicator color={theme.primary} size="large" />
      </View>
    );
  }

  if (error || !vendor) {
    return (
      <View style={[styles.center, { backgroundColor: theme.bg }]}>
        <Stack.Screen options={{ title: "Vendor" }} />
        <Text style={{ color: theme.danger, textAlign: "center", padding: 24 }}>
          {error || "Vendor not found."}
        </Text>
      </View>
    );
  }

  const mainContact = (vendor.contacts || []).find((c) => c.is_main) || (vendor.contacts || [])[0];

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
          title: vendor.name || "Vendor",
          headerRight: () => (
            <Pressable
              onPress={() => router.push({ pathname: "/vendor-form", params: { id: vendor._id || id } })}
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
            {vendor.name || "—"}
          </Text>
          {vendor.is_active === false ? <Badge label="Inactive" tone="muted" /> : null}
        </View>
        {mainContact ? (
          <KV
            label="Contact"
            value={[mainContact.first_name, mainContact.last_name].filter(Boolean).join(" ")}
          />
        ) : null}
        <KV label="Phone" value={mainContact?.phone || ""} />
        <KV label="Email" value={mainContact?.email || ""} />
        <KV label="Website" value={vendor.website || ""} />
        <KV label="Address" value={vendor.address || ""} />
        {vendor.notes ? <KV label="Notes" value={vendor.notes} /> : null}
      </RowCard>

      {summary ? (
        <>
          <Text style={[styles.sectionTitle, { color: theme.muted }]}>ORDERS SUMMARY</Text>
          <RowCard>
            <KV label="Total orders" value={String(summary.total_orders)} />
            <KV label="Total amount" value={money(summary.total_amount)} />
            <KV label="Paid" value={money(summary.total_paid)} />
            <KV label="Unpaid" value={money(summary.unpaid)} />
            <KV
              label="Received"
              value={`${summary.received} of ${summary.received + summary.not_received}`}
            />
          </RowCard>
        </>
      ) : null}

      <Text style={[styles.sectionTitle, { color: theme.muted }]}>PART ORDERS</Text>
      {orders.length === 0 ? (
        <RowCard>
          <Text style={{ color: theme.muted, fontSize: 13 }}>No part orders for this vendor.</Text>
        </RowCard>
      ) : (
        orders.map((o) => (
          <RowCard key={o.id}>
            <View style={styles.headerRow}>
              <Text style={{ color: theme.text, fontSize: 14, fontWeight: "700" }}>
                Order #{o.order_number || "—"}
              </Text>
              {o.status === "received" ? (
                <Badge label="Received" tone="success" />
              ) : (
                <Badge label={o.status || "ordered"} tone="warning" />
              )}
            </View>
            <View style={styles.headerRow}>
              <Text style={{ color: theme.muted, fontSize: 13 }}>
                {o.items_count} item{o.items_count === 1 ? "" : "s"} · {o.created_at || "—"}
              </Text>
              <Text style={{ color: theme.text, fontSize: 14, fontWeight: "700" }}>
                {money(o.total_amount)}
              </Text>
            </View>
          </RowCard>
        ))
      )}

      {hasMore ? (
        <Pressable
          style={[styles.moreBtn, { borderColor: theme.border, backgroundColor: theme.surface }]}
          onPress={loadMore}
          disabled={loadingMore}
        >
          {loadingMore ? (
            <ActivityIndicator color={theme.primary} size="small" />
          ) : (
            <Text style={{ color: theme.text, fontWeight: "600" }}>Load more</Text>
          )}
        </Pressable>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 12, paddingBottom: 40 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 },
  title: { fontSize: 17, fontWeight: "800", flexShrink: 1 },
  sectionTitle: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: 14, marginBottom: 2 },
  moreBtn: {
    borderWidth: 1,
    borderRadius: 12,
    paddingVertical: 12,
    alignItems: "center",
    marginTop: 10,
  },
});
