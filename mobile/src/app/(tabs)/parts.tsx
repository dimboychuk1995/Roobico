import AsyncStorage from "@react-native-async-storage/async-storage";
import { useRouter } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { ListScreen } from "@/components/list-screen";
import { Badge, RowCard } from "@/components/ui";
import {
  PartRow,
  PartStockLevel,
  PartsListResponse,
  PartsOrderRow,
  StocktakeRow,
  fetchParts,
  fetchPartsOrders,
  fetchStocktakes,
  money,
  stockLabel,
  stockQty,
} from "@/lib/api";
import { useHasPermission } from "@/context/auth";
import { useTheme } from "@/lib/theme";

// Фильтр «ниже / близко к минимуму» помним между запусками, как вид календаря.
const STOCK_LEVEL_KEY = "roobico.parts.stockLevel";

type StockFilter = PartStockLevel | "";

const STOCK_FILTERS: { key: StockFilter; label: string; tone: "primary" | "danger" | "warning" }[] = [
  { key: "", label: "All", tone: "primary" },
  { key: "below", label: "Below min", tone: "danger" },
  { key: "near", label: "Near min", tone: "warning" },
];

const STOCK_EMPTY: Record<StockFilter, { title: string; hint: string }> = {
  "": { title: "No parts in stock", hint: "Parts with stock on hand will appear here." },
  below: {
    title: "No parts below minimum",
    hint: "Parts whose stock dropped under their minimum will show up here.",
  },
  near: {
    title: "No parts near minimum",
    hint: "Parts at or close to their minimum stock will show up here.",
  },
};

function isStockLevel(value: unknown): value is PartStockLevel {
  return value === "below" || value === "near";
}

/** Бейдж состояния запаса относительно минимума; null — минимум не задан или запаса хватает. */
function StockStateBadge({ state }: { state: PartRow["stock_state"] }) {
  if (state === "below") return <Badge label="Below min" tone="danger" />;
  if (state === "near") return <Badge label="Near min" tone="warning" />;
  return null;
}

function PartCard({ item }: { item: PartRow }) {
  const theme = useTheme();
  // Остаток относительно минимума: ниже — красным, близко — жёлтым.
  const state = item.stock_state ?? null;
  const stockColor = state === "below" ? theme.danger : state === "near" ? theme.warning : theme.primary;
  const meta = [
    item.min_stock_enabled ? `Min ${stockQty(item.min_stock, item.unit_short)}` : "",
    item.reference ? `Ref ${item.reference}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <RowCard>
      <View style={styles.topRow}>
        <Text style={[styles.number, { color: theme.text }]} numberOfLines={1}>
          {item.part_number}
        </Text>
        <View style={styles.stockWrap}>
          <StockStateBadge state={state} />
          <Text style={[styles.stock, { color: stockColor }]}>
            {stockLabel(item.in_stock, item.unit_short)}
          </Text>
        </View>
      </View>
      {item.description ? (
        <Text style={[styles.desc, { color: theme.muted }]} numberOfLines={2}>
          {item.description}
        </Text>
      ) : null}
      {item.cross_refs && item.cross_refs.length > 0 ? (
        <Text style={[styles.crossRefs, { color: theme.muted }]} numberOfLines={1}>
          ⇄ {item.cross_refs.map((x) => x.part_number).join(", ")}
        </Text>
      ) : null}
      <View style={styles.bottomRow}>
        <Text style={[styles.cost, { color: theme.text }]}>Avg cost {money(item.average_cost)}</Text>
        {meta ? (
          <Text style={[styles.ref, { color: theme.muted }]} numberOfLines={1}>
            {meta}
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
  const canCreateOrder = useHasPermission("parts_orders.create");
  const canAdjustStock = useHasPermission("parts.adjust_stock");
  const [segment, setSegment] = useState<"stock" | "orders" | "counts">("stock");
  const [stockLevel, setStockLevel] = useState<StockFilter>("");
  const [stockPrefReady, setStockPrefReady] = useState(false);
  const [counts, setCounts] = useState<PartsListResponse["counts"]>(undefined);

  useEffect(() => {
    (async () => {
      try {
        const saved = await AsyncStorage.getItem(STOCK_LEVEL_KEY);
        if (isStockLevel(saved)) setStockLevel(saved);
      } catch {
        // нет стораджа — фильтр по умолчанию «All»
      } finally {
        setStockPrefReady(true);
      }
    })();
  }, []);

  const changeStockLevel = (level: StockFilter) => {
    setStockLevel(level);
    AsyncStorage.setItem(STOCK_LEVEL_KEY, level).catch(() => {});
  };

  // Счётчики для чипов приходят вместе с каждой страницей списка.
  const fetchStockPage = useCallback(
    (q: string, page: number) =>
      fetchParts(q, page, { stockLevel }).then((data) => {
        if (data.counts) setCounts(data.counts);
        return data;
      }),
    [stockLevel]
  );

  const stockFilterRow = (
    <View style={styles.filterRow}>
      {STOCK_FILTERS.map((f) => {
        const active = stockLevel === f.key;
        const count = f.key ? counts?.[f.key] ?? 0 : 0;
        const color =
          f.tone === "danger" ? theme.danger : f.tone === "warning" ? theme.warning : theme.primary;
        // На янтарном фоне белый текст не читается — тёмный в обеих темах.
        const textColor = !active ? theme.text : f.tone === "warning" ? "#1a2e22" : "#fff";
        return (
          <Pressable
            key={f.key || "all"}
            onPress={() => changeStockLevel(f.key)}
            accessibilityRole="button"
            accessibilityState={{ selected: active }}
            style={[
              styles.filterChip,
              {
                backgroundColor: active ? color : theme.surface,
                borderColor: active ? color : theme.border,
              },
            ]}
          >
            <Text style={{ color: textColor, fontWeight: "600", fontSize: 12 }}>
              {count > 0 ? `${f.label} · ${count}` : f.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );

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
      {segment === "orders" && canCreateOrder ? (
        <Pressable
          onPress={() => router.push("/parts-order-form")}
          style={[styles.segmentChip, { borderColor: theme.primary, marginLeft: "auto" }]}
        >
          <Text style={{ color: theme.primary, fontWeight: "700", fontSize: 13 }}>+ New order</Text>
        </Pressable>
      ) : null}
      {segment === "counts" && canAdjustStock ? (
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

  // Пока не прочитан сохранённый фильтр — список не стартуем, иначе он
  // загрузился бы дважды (сначала «All», потом сохранённый).
  if (!stockPrefReady) {
    return <View style={{ flex: 1, backgroundColor: theme.bg }} />;
  }

  // key по фильтру: смена чипа перемонтирует список и грузит первую страницу
  // уже с новым stock_level; pull-to-refresh и подгрузка идут через тот же fetchPage.
  return (
    <ListScreen<PartRow>
      key={`stock-${stockLevel}`}
      fetchPage={fetchStockPage}
      renderItem={(item) => (
        <Pressable onPress={() => router.push({ pathname: "/part/[id]", params: { id: item.id } })}>
          <PartCard item={item} />
        </Pressable>
      )}
      keyExtractor={(item) => item.id}
      searchPlaceholder={stockLevel ? "Search parts..." : "Search parts in stock..."}
      emptyTitle={STOCK_EMPTY[stockLevel].title}
      emptyHint={STOCK_EMPTY[stockLevel].hint}
      header={
        <>
          {segmentBar}
          {stockFilterRow}
        </>
      }
    />
  );
}

const styles = StyleSheet.create({
  topRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 },
  number: { fontSize: 15, fontWeight: "700", flexShrink: 1 },
  stockWrap: { flexDirection: "row", alignItems: "center", gap: 8, flexShrink: 0 },
  stock: { fontSize: 15, fontWeight: "800" },
  desc: { fontSize: 13 },
  crossRefs: { fontSize: 12, marginTop: 2 },
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
  filterRow: {
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 12,
    paddingBottom: 6,
    alignItems: "center",
  },
  filterChip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 4 },
});
