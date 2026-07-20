import { useRouter } from "expo-router";
import { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { ListScreen } from "@/components/list-screen";
import { Badge, RowCard } from "@/components/ui";
import {
  PartRow,
  PartsOrderRow,
  StocktakeRow,
  fetchParts,
  fetchPartsOrders,
  fetchStocktakes,
  money,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

function PartCard({ item }: { item: PartRow }) {
  const theme = useTheme();
  return (
    <RowCard>
      <View style={styles.topRow}>
        <Text style={[styles.number, { color: theme.text }]} numberOfLines={1}>
          {item.part_number}
        </Text>
        <Text style={[styles.stock, { color: theme.primary }]}>×{item.in_stock}</Text>
      </View>
      {item.description ? (
        <Text style={[styles.desc, { color: theme.muted }]} numberOfLines={2}>
          {item.description}
        </Text>
      ) : null}
      <View style={styles.bottomRow}>
        <Text style={[styles.cost, { color: theme.text }]}>Avg cost {money(item.average_cost)}</Text>
        {item.reference ? (
          <Text style={[styles.ref, { color: theme.muted }]} numberOfLines={1}>
            Ref {item.reference}
          </Text>
        ) : null}
      </View>
    </RowCard>
  );
}

function OrderCard({ item }: { item: PartsOrderRow }) {
  const theme = useTheme();
  const isReturn = !!item.is_return;
  return (
    <RowCard>
      <View style={styles.topRow}>
        <Text style={[styles.number, { color: theme.text }]}>
          {isReturn ? `Return #${item.order_number ?? "—"}` : `Order #${item.order_number ?? "—"}`}
        </Text>
        {isReturn ? (
          <Badge label={`Return · #${item.return_for_order_number ?? "—"}`} tone="danger" />
        ) : item.status === "received" ? (
          <Badge label="Received" tone="success" />
        ) : (
          <Badge label={item.status || "ordered"} tone="warning" />
        )}
      </View>
      <Text style={[styles.desc, { color: theme.text }]} numberOfLines={1}>
        {item.vendor}
      </Text>
      <View style={styles.bottomRow}>
        <Text style={{ color: theme.muted, fontSize: 13 }}>
          {item.items_count} item{item.items_count === 1 ? "" : "s"} · {item.created_at}
        </Text>
        <Text
          style={[
            styles.cost,
            { color: isReturn ? theme.danger : theme.text, fontWeight: "700" },
          ]}
        >
          {isReturn ? `-${money(Math.abs(item.total_amount))}` : money(item.total_amount)}
        </Text>
      </View>
      {!isReturn && item.payment_status !== "paid" ? (
        <Text style={{ color: theme.warning ?? "#b45309", fontSize: 12, fontWeight: "600" }}>
          Unpaid {money(item.remaining_balance)}
        </Text>
      ) : null}
    </RowCard>
  );
}

function StocktakeCard({ item }: { item: StocktakeRow }) {
  const theme = useTheme();
  const scope =
    [item.scope_location, item.scope_category].filter(Boolean).join(" · ") || "Whole warehouse";
  return (
    <RowCard>
      <View style={styles.topRow}>
        <Text style={[styles.number, { color: theme.text }]}>
          ST-{item.number}
          {item.name ? ` · ${item.name}` : ""}
        </Text>
        {item.status === "open" ? (
          <Badge label="Open" tone="info" />
        ) : item.status === "completed" ? (
          <Badge label="Completed" tone="success" />
        ) : (
          <Badge label="Cancelled" tone="muted" />
        )}
      </View>
      <Text style={[styles.desc, { color: theme.muted }]} numberOfLines={1}>
        {scope}
      </Text>
      <View style={styles.bottomRow}>
        <Text style={{ color: theme.muted, fontSize: 13 }}>
          Counted {item.items_counted}/{item.items_total} · {item.created_label}
        </Text>
        {item.status === "completed" ? (
          <Text style={{ color: theme.text, fontSize: 13, fontWeight: "600" }}>
            −{money(item.shortage_value)} / +{money(item.overage_value)}
          </Text>
        ) : null}
      </View>
    </RowCard>
  );
}

export default function PartsScreen() {
  const theme = useTheme();
  const router = useRouter();
  const [segment, setSegment] = useState<"stock" | "orders" | "counts">("stock");

  const segmentBar = (
    <View style={styles.segmentRow}>
      {(["stock", "orders", "counts"] as const).map((s) => {
        const active = segment === s;
        return (
          <Pressable
            key={s}
            onPress={() => setSegment(s)}
            style={[
              styles.segmentChip,
              {
                backgroundColor: active ? theme.primary : theme.surface,
                borderColor: active ? theme.primary : theme.border,
              },
            ]}
          >
            <Text style={{ color: active ? "#fff" : theme.text, fontWeight: "600", fontSize: 13 }}>
              {s === "stock" ? "Stock" : s === "orders" ? "Orders" : "Counts"}
            </Text>
          </Pressable>
        );
      })}
      {segment === "orders" ? (
        <Pressable
          onPress={() => router.push("/parts-order-form")}
          style={[styles.segmentChip, { borderColor: theme.primary, marginLeft: "auto" }]}
        >
          <Text style={{ color: theme.primary, fontWeight: "700", fontSize: 13 }}>+ New order</Text>
        </Pressable>
      ) : null}
      {segment === "counts" ? (
        <Pressable
          onPress={() => router.push("/stocktake-form")}
          style={[styles.segmentChip, { borderColor: theme.primary, marginLeft: "auto" }]}
        >
          <Text style={{ color: theme.primary, fontWeight: "700", fontSize: 13 }}>+ New count</Text>
        </Pressable>
      ) : null}
    </View>
  );

  if (segment === "orders") {
    return (
      <ListScreen<PartsOrderRow>
        key="orders"
        fetchPage={fetchPartsOrders}
        renderItem={(item) => (
          <Pressable
            onPress={() => router.push({ pathname: "/parts-order/[id]", params: { id: item.id } })}
          >
            <OrderCard item={item} />
          </Pressable>
        )}
        keyExtractor={(item) => item.id}
        searchPlaceholder="Search orders..."
        emptyTitle="No parts orders found"
        emptyHint="Create an order to track vendor purchases."
        header={segmentBar}
      />
    );
  }

  if (segment === "counts") {
    return (
      <ListScreen<StocktakeRow>
        key="counts"
        fetchPage={fetchStocktakes}
        renderItem={(item) => (
          <Pressable
            onPress={() => router.push({ pathname: "/stocktake/[id]", params: { id: item.id } })}
          >
            <StocktakeCard item={item} />
          </Pressable>
        )}
        keyExtractor={(item) => item.id}
        searchPlaceholder="Search stocktakes..."
        emptyTitle="No stocktakes yet"
        emptyHint="Start a count to reconcile physical inventory — the warehouse keeps working while you count."
        header={segmentBar}
      />
    );
  }

  return (
    <ListScreen<PartRow>
      key="stock"
      fetchPage={fetchParts}
      renderItem={(item) => (
        <Pressable onPress={() => router.push({ pathname: "/part/[id]", params: { id: item.id } })}>
          <PartCard item={item} />
        </Pressable>
      )}
      keyExtractor={(item) => item.id}
      searchPlaceholder="Search parts in stock..."
      emptyTitle="No parts in stock"
      emptyHint="Parts with stock on hand will appear here."
      header={segmentBar}
    />
  );
}

const styles = StyleSheet.create({
  topRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 },
  number: { fontSize: 15, fontWeight: "700", flexShrink: 1 },
  stock: { fontSize: 15, fontWeight: "800" },
  desc: { fontSize: 13 },
  bottomRow: { flexDirection: "row", justifyContent: "space-between", gap: 8, marginTop: 2 },
  cost: { fontSize: 13, fontWeight: "500" },
  ref: { fontSize: 12, flexShrink: 1 },
  segmentRow: {
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 6,
    alignItems: "center",
  },
  segmentChip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 14, paddingVertical: 6 },
});
