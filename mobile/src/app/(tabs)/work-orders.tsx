import { useRouter } from "expo-router";
import { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { ListScreen } from "@/components/list-screen";
import { Badge, RowCard } from "@/components/ui";
import {
  AllPaymentRow,
  WorkOrderRow,
  fetchAllPayments,
  fetchEstimates,
  fetchWorkOrders,
  money,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

const SEGMENTS = [
  { key: "work_orders", label: "Work Orders" },
  { key: "payments", label: "Payments" },
  { key: "estimates", label: "Estimates" },
] as const;

type SegmentKey = (typeof SEGMENTS)[number]["key"];

function statusBadge(item: WorkOrderRow) {
  if (item.is_paid) return <Badge label="Paid" tone="success" />;
  if (item.is_in_progress) return <Badge label="In Progress" tone="info" />;
  return <Badge label="Unpaid" tone="warning" />;
}

function WorkOrderCard({ item }: { item: WorkOrderRow }) {
  const theme = useTheme();
  return (
    <RowCard>
      <View style={styles.topRow}>
        <Text style={[styles.number, { color: theme.text }]}>WO #{item.wo_number ?? "—"}</Text>
        {statusBadge(item)}
      </View>
      <Text style={[styles.customer, { color: theme.text }]} numberOfLines={1}>
        {item.customer}
      </Text>
      <Text style={[styles.meta, { color: theme.muted }]} numberOfLines={1}>
        {item.unit !== "-" ? `${item.unit} · ` : ""}
        {item.date}
      </Text>
      <View style={styles.totalsRow}>
        <Text style={[styles.total, { color: theme.text }]}>{money(item.grand_total)}</Text>
        {item.balance > 0 ? (
          <Text style={[styles.balance, { color: theme.warning }]}>
            Balance {money(item.balance)}
          </Text>
        ) : null}
      </View>
    </RowCard>
  );
}

function PaymentCard({ item }: { item: AllPaymentRow }) {
  const theme = useTheme();
  const date = item.payment_date ? item.payment_date.slice(0, 10) : "—";
  return (
    <RowCard>
      <View style={styles.topRow}>
        <Text style={[styles.number, { color: theme.text }]}>{money(item.amount)}</Text>
        <Badge label={item.payment_method} tone="muted" />
      </View>
      <Text style={[styles.customer, { color: theme.text }]} numberOfLines={1}>
        WO #{item.wo_number} · {item.customer}
      </Text>
      <Text style={[styles.meta, { color: theme.muted }]} numberOfLines={1}>
        {date}
        {item.notes ? ` · ${item.notes}` : ""}
      </Text>
    </RowCard>
  );
}

export default function WorkOrdersScreen() {
  const theme = useTheme();
  const router = useRouter();
  const [segment, setSegment] = useState<SegmentKey>("work_orders");

  const openWo = (woId: string) => {
    if (woId) router.push({ pathname: "/work-order/[id]", params: { id: woId } });
  };

  const segmentBar = (
    <View style={styles.segmentRow}>
      {SEGMENTS.map((s) => {
        const active = s.key === segment;
        return (
          <Pressable
            key={s.key}
            onPress={() => setSegment(s.key)}
            style={[
              styles.segmentChip,
              {
                backgroundColor: active ? theme.primary : theme.surface,
                borderColor: active ? theme.primary : theme.border,
              },
            ]}
          >
            <Text style={{ color: active ? "#fff" : theme.text, fontWeight: "600", fontSize: 13 }}>
              {s.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );

  if (segment === "payments") {
    return (
      <ListScreen<AllPaymentRow>
        key="payments"
        fetchPage={fetchAllPayments}
        renderItem={(item) => (
          <Pressable onPress={() => openWo(item.work_order_id)}>
            <PaymentCard item={item} />
          </Pressable>
        )}
        keyExtractor={(item) => item.id}
        searchPlaceholder="Search payments..."
        emptyTitle="No payments found"
        emptyHint="Payments will appear here once work orders are paid."
        header={segmentBar}
      />
    );
  }

  if (segment === "estimates") {
    return (
      <ListScreen<WorkOrderRow>
        key="estimates"
        fetchPage={fetchEstimates}
        renderItem={(item) => (
          <Pressable onPress={() => openWo(item.id)}>
            <WorkOrderCard item={item} />
          </Pressable>
        )}
        keyExtractor={(item) => item.id}
        searchPlaceholder="Search estimates..."
        emptyTitle="No estimates found"
        emptyHint="Estimates you create will show up here."
        header={segmentBar}
      />
    );
  }

  return (
    <ListScreen<WorkOrderRow>
      key="work_orders"
      fetchPage={(q, page) => fetchWorkOrders(q, page)}
      renderItem={(item) => (
        <Pressable onPress={() => openWo(item.id)}>
          <WorkOrderCard item={item} />
        </Pressable>
      )}
      keyExtractor={(item) => item.id}
      searchPlaceholder="Search work orders..."
      emptyTitle="No work orders found"
      emptyHint="Try a different search."
      header={segmentBar}
    />
  );
}

const styles = StyleSheet.create({
  topRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  number: { fontSize: 15, fontWeight: "700" },
  customer: { fontSize: 14, fontWeight: "500" },
  meta: { fontSize: 13 },
  totalsRow: { flexDirection: "row", justifyContent: "space-between", marginTop: 2 },
  total: { fontSize: 15, fontWeight: "700" },
  balance: { fontSize: 13, fontWeight: "600" },
  segmentRow: { flexDirection: "row", gap: 8, paddingHorizontal: 12, paddingVertical: 6 },
  segmentChip: {
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 6,
  },
});
