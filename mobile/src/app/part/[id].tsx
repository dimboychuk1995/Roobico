import { Stack, useLocalSearchParams, useRouter } from "expo-router";
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

import { Badge, KV, RowCard } from "@/components/ui";
import {
  ApiError,
  PartFull,
  PartHistoryOrder,
  fetchPartFull,
  fetchPartHistory,
  money,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

export default function PartDetailsScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const theme = useTheme();
  const router = useRouter();

  const [part, setPart] = useState<PartFull | null>(null);
  const [history, setHistory] = useState<PartHistoryOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const [p, h] = await Promise.all([
        fetchPartFull(id),
        fetchPartHistory(id).catch(() => null),
      ]);
      setPart(p);
      setHistory(h?.orders || []);
      setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load part.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return (
      <View style={[styles.center, { backgroundColor: theme.bg }]}>
        <Stack.Screen options={{ title: "Part" }} />
        <ActivityIndicator color={theme.primary} size="large" />
      </View>
    );
  }

  if (error || !part) {
    return (
      <View style={[styles.center, { backgroundColor: theme.bg }]}>
        <Stack.Screen options={{ title: "Part" }} />
        <Text style={{ color: theme.danger, textAlign: "center", padding: 24 }}>
          {error || "Part not found."}
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
      <Stack.Screen options={{ title: part.part_number || "Part" }} />

      <RowCard>
        <View style={styles.headerRow}>
          <Text style={[styles.title, { color: theme.text }]} numberOfLines={2}>
            {part.part_number}
          </Text>
          <Text style={[styles.stock, { color: theme.primary }]}>×{part.in_stock}</Text>
        </View>
        <KV label="Description" value={part.description} />
        <KV label="Reference" value={part.reference} />
        <KV label="Average cost" value={money(part.average_cost)} />
        {part.has_selling_price ? <KV label="Selling price" value={money(part.selling_price)} /> : null}
        {part.core_has_charge ? <KV label="Core charge" value={money(part.core_cost)} /> : null}
        {part.do_not_track_inventory ? (
          <View style={{ marginTop: 4 }}>
            <Badge label="Inventory not tracked" tone="muted" />
          </View>
        ) : null}
      </RowCard>

      {part.cross_refs && part.cross_refs.length > 0 ? (
        <>
          <Text style={[styles.sectionTitle, { color: theme.muted }]}>
            CROSS REFERENCES ({part.cross_refs.length})
          </Text>
          {part.cross_refs.map((x) => (
            <Pressable
              key={x.id}
              onPress={() => router.push({ pathname: "/part/[id]", params: { id: x.id } })}
            >
              <RowCard>
                <View style={styles.headerRow}>
                  <Text style={{ color: theme.text, fontSize: 14, fontWeight: "700" }}>
                    ⇄ {x.part_number}
                  </Text>
                  <Text style={{ color: theme.primary, fontSize: 14, fontWeight: "800" }}>
                    ×{x.in_stock}
                  </Text>
                </View>
                {x.description ? (
                  <Text style={{ color: theme.muted, fontSize: 13 }} numberOfLines={1}>
                    {x.description}
                  </Text>
                ) : null}
              </RowCard>
            </Pressable>
          ))}
        </>
      ) : null}

      <Text style={[styles.sectionTitle, { color: theme.muted }]}>
        ORDER HISTORY ({history.length})
      </Text>
      {history.length === 0 ? (
        <RowCard>
          <Text style={{ color: theme.muted, fontSize: 13 }}>No orders with this part yet.</Text>
        </RowCard>
      ) : (
        history.map((o) => (
          <RowCard key={o.order_id}>
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
            <KV label="Vendor" value={o.vendor} />
            <View style={styles.headerRow}>
              <Text style={{ color: theme.muted, fontSize: 13 }}>
                ×{o.quantity} @ {money(o.price)}
              </Text>
              <Text style={{ color: theme.text, fontSize: 14, fontWeight: "700" }}>
                {money(o.quantity * o.price)}
              </Text>
            </View>
          </RowCard>
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
  stock: { fontSize: 17, fontWeight: "800" },
  sectionTitle: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: 14, marginBottom: 2 },
});
