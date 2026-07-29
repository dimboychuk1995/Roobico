import Ionicons from "@expo/vector-icons/Ionicons";
import { useFocusEffect, useRouter } from "expo-router";
import { useCallback, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { ListScreen } from "@/components/list-screen";
import { Badge, RowCard } from "@/components/ui";
import { useIsMechanic } from "@/context/auth";
import { useToast } from "@/context/toast";
import {
  ActiveTimerItem,
  AllPaymentRow,
  UnitSearchRow,
  WorkOrderRow,
  fetchActiveTimers,
  fetchAllPayments,
  fetchEstimates,
  fetchWorkOrders,
  money,
  searchUnits,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

const SEGMENTS = [
  { key: "work_orders", label: "Work Orders" },
  { key: "payments", label: "Payments" },
  { key: "estimates", label: "Estimates" },
] as const;

type SegmentKey = (typeof SEGMENTS)[number]["key"];

function statusBadge(item: WorkOrderRow, isMechanic: boolean) {
  // Механик видит только «свои» статусы (open/in_progress) и подтверждение —
  // никаких paid/unpaid (paid WO ему сервер вообще не отдаёт).
  if (isMechanic) {
    if (item.manager_confirmed) return <Badge label="Confirmed" tone="success" />;
    if (item.is_in_progress) return <Badge label="In Progress" tone="info" />;
    return <Badge label="Open" tone="muted" />;
  }
  if (item.is_paid) return <Badge label="Paid" tone="success" />;
  if (item.is_in_progress) return <Badge label="In Progress" tone="info" />;
  return <Badge label="Unpaid" tone="warning" />;
}

function WorkOrderCard({ item }: { item: WorkOrderRow }) {
  const theme = useTheme();
  const isMechanic = useIsMechanic();
  return (
    <RowCard>
      <View style={styles.topRow}>
        <Text style={[styles.number, { color: theme.text }]}>WO #{item.wo_number ?? "—"}</Text>
        <View style={styles.badgeRow}>
          {item.mechanic_done ? <Badge label="Done" tone="success" /> : null}
          {!isMechanic && item.manager_confirmed ? <Badge label="Confirmed" tone="success" /> : null}
          {statusBadge(item, isMechanic)}
        </View>
      </View>
      <Text style={[styles.customer, { color: theme.text }]} numberOfLines={1}>
        {item.customer}
      </Text>
      <Text style={[styles.meta, { color: theme.muted }]} numberOfLines={1}>
        {item.unit !== "-" ? `${item.unit} · ` : ""}
        {item.date}
      </Text>
      {item.working_now && item.working_now.length ? (
        <Text style={[styles.meta, { color: theme.primary }]} numberOfLines={1}>
          ● {item.working_now.join(", ")}
        </Text>
      ) : null}
      {!isMechanic ? (
        <View style={styles.totalsRow}>
          <Text style={[styles.total, { color: theme.text }]}>{money(item.grand_total ?? 0)}</Text>
          {(item.balance ?? 0) > 0 ? (
            <Text style={[styles.balance, { color: theme.warning }]}>
              Balance {money(item.balance ?? 0)}
            </Text>
          ) : null}
        </View>
      ) : null}
    </RowCard>
  );
}

/** «Сейчас в работе»: идущие таймеры всех механиков магазина. */
function ActiveNowSection({
  items,
  onOpen,
}: {
  items: ActiveTimerItem[];
  onOpen: (woId: string) => void;
}) {
  const theme = useTheme();
  if (!items.length) return null;
  return (
    <View style={styles.activeNowWrap}>
      <Text style={[styles.activeNowTitle, { color: theme.muted }]}>● IN WORK NOW</Text>
      {items.map((t, idx) => (
        <Pressable key={`${t.work_order_id}-${t.user_id}-${idx}`} onPress={() => onOpen(t.work_order_id)}>
          <RowCard>
            <View style={styles.topRow}>
              <Text style={[styles.number, { color: theme.text }]}>WO #{t.wo_number ?? "—"}</Text>
              <Badge label="In Progress" tone="info" />
            </View>
            <Text style={[styles.customer, { color: theme.text }]} numberOfLines={1}>
              {t.customer}
              {t.labor_description ? ` · ${t.labor_description}` : ""}
            </Text>
            <Text style={[styles.meta, { color: theme.danger }]} numberOfLines={1}>
              ● {t.user_name || "—"}
              {t.mine ? " (you)" : ""}
            </Text>
          </RowCard>
        </Pressable>
      ))}
      <Text style={[styles.activeNowTitle, { color: theme.muted, marginTop: 8 }]}>ALL WORK ORDERS</Text>
    </View>
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
  const toast = useToast();
  const isMechanic = useIsMechanic();
  const [segment, setSegment] = useState<SegmentKey>("work_orders");
  const [activeTimers, setActiveTimers] = useState<ActiveTimerItem[]>([]);
  const [unitResults, setUnitResults] = useState<UnitSearchRow[]>([]);

  const openWo = (woId: string) => {
    if (woId) router.push({ pathname: "/work-order/[id]", params: { id: woId } });
  };

  // Подтверждённый менеджером WO механик открыть не может.
  const openWoRow = (item: WorkOrderRow) => {
    if (isMechanic && item.manager_confirmed) {
      toast.show("This work order was confirmed by a manager and is locked.", "info");
      return;
    }
    openWo(item.id);
  };

  // Общий поиск для механика: та же строка поиска ищет и юниты магазина,
  // чтобы по номеру юнита увидеть кастомера перед созданием WO.
  const onQueryChange = useCallback(
    (q: string) => {
      if (!isMechanic) return;
      if (q.length < 2) {
        setUnitResults([]);
        return;
      }
      searchUnits(q)
        .then((d) => setUnitResults((d.items || []).slice(0, 5)))
        .catch(() => setUnitResults([]));
    },
    [isMechanic]
  );

  // Механик видит сверху, какие WO прямо сейчас в работе у других механиков.
  useFocusEffect(
    useCallback(() => {
      if (!isMechanic) return;
      fetchActiveTimers()
        .then((d) => setActiveTimers(d.items || []))
        .catch(() => {});
    }, [isMechanic])
  );

  // Payments и Estimates — денежные разделы, механику не показываем.
  const segments = isMechanic ? SEGMENTS.filter((s) => s.key === "work_orders") : SEGMENTS;

  const segmentBar = segments.length < 2 ? null : (
    <View style={styles.segmentRow}>
      {segments.map((s) => {
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

  const workOrdersHeader = (
    <>
      {segmentBar}
      {isMechanic ? (
        <Pressable
          style={[styles.createBtn, { backgroundColor: theme.primary }]}
          onPress={() => router.push("/work-order-form")}
        >
          <Ionicons name="add-circle-outline" size={22} color="#fff" />
          <Text style={styles.createBtnText}>Create Work Order</Text>
        </Pressable>
      ) : null}
      {isMechanic && unitResults.length ? (
        <UnitSearchSection
          items={unitResults}
          onPick={(u) =>
            router.push({
              pathname: "/work-order-form",
              params: { customerId: u.customer_id, unitId: u.id },
            })
          }
        />
      ) : null}
      {isMechanic && activeTimers.length ? (
        <ActiveNowSection items={activeTimers} onOpen={openWo} />
      ) : null}
    </>
  );

  return (
    <ListScreen<WorkOrderRow>
      key="work_orders"
      fetchPage={(q, page) => fetchWorkOrders(q, page)}
      renderItem={(item) => (
        <Pressable onPress={() => openWoRow(item)}>
          <WorkOrderCard item={item} />
        </Pressable>
      )}
      keyExtractor={(item) => item.id}
      searchPlaceholder={isMechanic ? "Search units, WOs, customers..." : "Search work orders..."}
      emptyTitle="No work orders found"
      emptyHint="Try a different search."
      header={workOrdersHeader}
      onQueryChange={onQueryChange}
    />
  );
}

/** Результаты общего поиска юнитов: юнит + его кастомер, тап — новый WO. */
function UnitSearchSection({
  items,
  onPick,
}: {
  items: UnitSearchRow[];
  onPick: (u: UnitSearchRow) => void;
}) {
  const theme = useTheme();
  return (
    <View style={styles.activeNowWrap}>
      <Text style={[styles.activeNowTitle, { color: theme.muted }]}>UNITS</Text>
      {items.map((u) => (
        <Pressable key={u.id} onPress={() => onPick(u)}>
          <RowCard>
            <View style={styles.topRow}>
              <Text style={[styles.number, { color: theme.text, flex: 1 }]} numberOfLines={1}>
                {u.label}
              </Text>
              <Ionicons name="add-circle-outline" size={20} color={theme.primary} />
            </View>
            <Text style={[styles.meta, { color: theme.muted }]} numberOfLines={1}>
              {u.customer_label}
            </Text>
          </RowCard>
        </Pressable>
      ))}
      <Text style={[styles.activeNowTitle, { color: theme.muted, marginTop: 8 }]}>WORK ORDERS</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  topRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  badgeRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  createBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    borderRadius: 14,
    paddingVertical: 16,
    marginHorizontal: 12,
    marginTop: 8,
    marginBottom: 4,
  },
  createBtnText: { color: "#fff", fontSize: 16, fontWeight: "800" },
  number: { fontSize: 15, fontWeight: "700" },
  customer: { fontSize: 14, fontWeight: "500" },
  meta: { fontSize: 13 },
  totalsRow: { flexDirection: "row", justifyContent: "space-between", marginTop: 2 },
  total: { fontSize: 15, fontWeight: "700" },
  balance: { fontSize: 13, fontWeight: "600" },
  segmentRow: { flexDirection: "row", gap: 8, paddingHorizontal: 12, paddingVertical: 6 },
  activeNowWrap: { paddingHorizontal: 12, gap: 8 },
  activeNowTitle: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: 6 },
  segmentChip: {
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 6,
  },
});
