/**
 * Экран подсчёта инвентаризации: список позиций с фильтрами, тап по строке —
 * ввод фактического количества (expected снимается на момент ввода, поправки
 * применяются дельтами при завершении — склад продолжает работать).
 */
import Ionicons from "@expo/vector-icons/Ionicons";
import { Stack, useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";
import { useCallback, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Badge, RowCard } from "@/components/ui";
import { useToast } from "@/context/toast";
import {
  ApiError,
  StocktakeDetail,
  StocktakeItemRow,
  cancelStocktake,
  completeStocktake,
  countStocktakeItem,
  fetchStocktakeDetail,
  money,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

type FilterMode = "all" | "pending" | "counted" | "diff";

export default function StocktakeScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const theme = useTheme();
  const toast = useToast();
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const [st, setSt] = useState<StocktakeDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterMode>("all");
  const [countItem, setCountItem] = useState<StocktakeItemRow | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      setSt(await fetchStocktakeDetail(id));
      setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load stocktake.");
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

  const items = useMemo(() => {
    if (!st) return [];
    const q = query.trim().toLowerCase();
    return st.items.filter((it) => {
      if (filter === "pending" && it.status !== "pending") return false;
      if (filter === "counted" && it.status !== "counted") return false;
      if (filter === "diff" && !(it.status === "counted" && (it.variance ?? 0) !== 0)) return false;
      if (!q) return true;
      return `${it.part_number} ${it.description} ${it.location_path}`.toLowerCase().includes(q);
    });
  }, [st, query, filter]);

  const isOpen = st?.status === "open";

  const onComplete = () => {
    if (!st) return;
    const pending = st.items_total - st.items_counted;
    const buttons: any[] = [{ text: "Not yet", style: "cancel" }];
    const run = (zero: boolean) => async () => {
      setBusy(true);
      try {
        await completeStocktake(st.id, zero);
        toast.show("Stocktake completed — adjustments applied.", "success");
        load();
      } catch (e) {
        toast.show(e instanceof ApiError ? e.message : "Failed to complete.", "error");
      } finally {
        setBusy(false);
      }
    };
    if (pending > 0) {
      buttons.push({ text: `Keep ${pending} uncounted`, onPress: run(false) });
      buttons.push({ text: `Zero ${pending} uncounted`, style: "destructive", onPress: run(true) });
    } else {
      buttons.push({ text: "Complete & apply", onPress: run(false) });
    }
    Alert.alert(
      "Complete stocktake?",
      pending > 0
        ? `Counted discrepancies will be applied as adjustments. ${pending} line(s) are not counted — zero them (full inventory) or leave unchanged (cycle count)?`
        : "Counted discrepancies will be applied to inventory as adjustments.",
      buttons
    );
  };

  const onCancel = () => {
    if (!st) return;
    Alert.alert("Cancel stocktake?", "Counted quantities will be discarded, no adjustments applied.", [
      { text: "Keep counting", style: "cancel" },
      {
        text: "Cancel stocktake",
        style: "destructive",
        onPress: async () => {
          setBusy(true);
          try {
            await cancelStocktake(st.id);
            toast.show("Stocktake cancelled.", "success");
            router.back();
          } catch (e) {
            toast.show(e instanceof ApiError ? e.message : "Failed to cancel.", "error");
            setBusy(false);
          }
        },
      },
    ]);
  };

  if (loading) {
    return (
      <View style={[styles.center, { backgroundColor: theme.bg }]}>
        <Stack.Screen options={{ title: "Stocktake" }} />
        <ActivityIndicator color={theme.primary} size="large" />
      </View>
    );
  }

  if (error || !st) {
    return (
      <View style={[styles.center, { backgroundColor: theme.bg }]}>
        <Stack.Screen options={{ title: "Stocktake" }} />
        <Text style={{ color: theme.danger, textAlign: "center", padding: 24 }}>
          {error || "Stocktake not found."}
        </Text>
      </View>
    );
  }

  const scope =
    [st.scope_location, st.scope_category].filter(Boolean).join(" · ") || "Whole warehouse";

  const header = (
    <View style={{ gap: 8, paddingBottom: 4 }}>
      <RowCard>
        <View style={styles.headerRow}>
          <Text style={[styles.title, { color: theme.text }]}>
            ST-{st.number}
            {st.name ? ` · ${st.name}` : ""}
          </Text>
          {st.status === "open" ? (
            <Badge label="Open" tone="info" />
          ) : st.status === "completed" ? (
            <Badge label="Completed" tone="success" />
          ) : (
            <Badge label="Cancelled" tone="muted" />
          )}
        </View>
        <Text style={{ color: theme.muted, fontSize: 13 }}>
          {scope} · {st.created_label}
        </Text>
        <Text style={{ color: theme.text, fontSize: 13, marginTop: 2 }}>
          Counted {st.items_counted}/{st.items_total} · Discrepancies {st.discrepancies} · Value{" "}
          <Text style={{ color: st.variance_value < 0 ? theme.danger : theme.primary, fontWeight: "700" }}>
            {st.variance_value < 0 ? "-" : ""}
            {money(Math.abs(st.variance_value))}
          </Text>
        </Text>
        {st.status === "completed" && st.totals ? (
          <Text style={{ color: theme.muted, fontSize: 13 }}>
            Adjusted {st.totals.items_adjusted}
            {st.totals.items_zeroed ? ` · Zeroed ${st.totals.items_zeroed}` : ""} · Shortage{" "}
            {money(st.totals.shortage_value)} · Overage {money(st.totals.overage_value)}
          </Text>
        ) : null}
      </RowCard>

      {isOpen ? (
        <View style={styles.actionRow}>
          <Pressable
            style={[styles.actionBtn, { backgroundColor: theme.primary, opacity: busy ? 0.7 : 1 }]}
            onPress={onComplete}
            disabled={busy}
          >
            <Ionicons name="checkmark-done-outline" size={16} color="#fff" />
            <Text style={{ color: "#fff", fontWeight: "700", fontSize: 13 }}>Complete</Text>
          </Pressable>
          <Pressable
            style={[styles.actionBtn, { borderWidth: 1, borderColor: theme.border, backgroundColor: theme.surface }]}
            onPress={onCancel}
            disabled={busy}
          >
            <Text style={{ color: theme.danger, fontWeight: "600", fontSize: 13 }}>Cancel count</Text>
          </Pressable>
        </View>
      ) : null}

      <TextInput
        style={[styles.search, { backgroundColor: theme.surface, borderColor: theme.border, color: theme.text }]}
        placeholder="Filter by part #, description, location..."
        placeholderTextColor={theme.muted}
        value={query}
        onChangeText={setQuery}
        autoCapitalize="none"
        autoCorrect={false}
      />

      <View style={styles.chipRow}>
        {(
          [
            ["all", "All"],
            ["pending", "Pending"],
            ["counted", "Counted"],
            ["diff", "Diff"],
          ] as [FilterMode, string][]
        ).map(([mode, label]) => {
          const active = filter === mode;
          return (
            <Pressable
              key={mode}
              onPress={() => setFilter(mode)}
              style={[
                styles.chip,
                {
                  backgroundColor: active ? theme.primary : theme.surface,
                  borderColor: active ? theme.primary : theme.border,
                },
              ]}
            >
              <Text style={{ color: active ? "#fff" : theme.text, fontSize: 12, fontWeight: "600" }}>
                {label}
              </Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );

  return (
    <View style={{ flex: 1, backgroundColor: theme.bg }}>
      <Stack.Screen options={{ title: `Stocktake ST-${st.number}` }} />
      <FlatList
        data={items}
        keyExtractor={(it) => it.id}
        ListHeaderComponent={header}
        contentContainerStyle={{ padding: 12, paddingBottom: insets.bottom + 24 }}
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
        ListEmptyComponent={
          <Text style={{ color: theme.muted, textAlign: "center", paddingVertical: 32 }}>
            No lines match the filter.
          </Text>
        }
        renderItem={({ item }) => (
          <Pressable onPress={() => (isOpen ? setCountItem(item) : undefined)}>
            <RowCard>
              <View style={styles.headerRow}>
                <Text style={{ color: theme.text, fontWeight: "700", fontSize: 14, flex: 1 }} numberOfLines={1}>
                  {item.part_number}
                </Text>
                {item.auto_zeroed ? (
                  <Badge label="Zeroed" tone="warning" />
                ) : item.status === "counted" ? (
                  <Badge label="Counted" tone="success" />
                ) : (
                  <Badge label="Pending" tone="muted" />
                )}
                {item.needs_recount ? <Badge label="Recount" tone="warning" /> : null}
              </View>
              {item.description ? (
                <Text style={{ color: theme.muted, fontSize: 13 }} numberOfLines={1}>
                  {item.description}
                </Text>
              ) : null}
              <View style={styles.headerRow}>
                <Text style={{ color: theme.muted, fontSize: 12, flex: 1 }} numberOfLines={1}>
                  {item.location_path}
                </Text>
                <Text style={{ color: theme.text, fontSize: 13 }}>
                  Expected {item.expected}
                  {item.counted_qty !== null ? ` · Counted ${item.counted_qty}` : ""}
                </Text>
              </View>
              {item.variance !== null && item.variance !== 0 ? (
                <Text
                  style={{
                    color: item.variance < 0 ? theme.danger : theme.primary,
                    fontWeight: "700",
                    fontSize: 13,
                  }}
                >
                  {item.variance > 0 ? "+" : ""}
                  {item.variance} ({item.variance_value !== null && item.variance_value < 0 ? "-" : "+"}
                  {money(Math.abs(item.variance_value ?? 0))})
                </Text>
              ) : null}
            </RowCard>
          </Pressable>
        )}
      />

      <CountModal
        item={countItem}
        stocktakeId={st.id}
        onClose={() => setCountItem(null)}
        onSaved={() => {
          setCountItem(null);
          load();
        }}
      />
    </View>
  );
}

function CountModal({
  item,
  stocktakeId,
  onClose,
  onSaved,
}: {
  item: StocktakeItemRow | null;
  stocktakeId: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const theme = useTheme();
  const toast = useToast();
  const [qty, setQty] = useState("");
  const [busy, setBusy] = useState(false);

  // При открытии модалки подставляем прошлый факт (пересчёт).
  const [lastItemId, setLastItemId] = useState<string | null>(null);
  if (item && item.id !== lastItemId) {
    setLastItemId(item.id);
    setQty(item.counted_qty !== null ? String(item.counted_qty) : "");
  }

  const onSubmit = async () => {
    if (!item) return;
    const num = parseInt(qty, 10);
    if (!Number.isFinite(num) || num < 0) {
      toast.show("Enter the counted quantity.", "error");
      return;
    }
    setBusy(true);
    try {
      const res = await countStocktakeItem(stocktakeId, item.id, num);
      const v = res.item.variance;
      toast.show(v === 0 ? "Counted — no discrepancy." : `Counted — variance ${v > 0 ? "+" : ""}${v}.`, "success");
      onSaved();
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Failed to save count.", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal visible={!!item} transparent animationType="fade" onRequestClose={onClose}>
      <KeyboardAvoidingView
        style={styles.modalBackdrop}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <View style={[styles.modalCard, { backgroundColor: theme.surface, borderColor: theme.border }]}>
          <Text style={[styles.title, { color: theme.text }]}>{item?.part_number}</Text>
          {item?.description ? (
            <Text style={{ color: theme.muted, fontSize: 13 }}>{item.description}</Text>
          ) : null}
          <Text style={{ color: theme.muted, fontSize: 13, marginTop: 2 }}>
            {item?.location_path} · Expected {item?.expected}
          </Text>
          <Text style={[styles.inputLabel, { color: theme.muted }]}>COUNTED QUANTITY</Text>
          <TextInput
            style={[styles.input, { backgroundColor: theme.surfaceSoft, borderColor: theme.border, color: theme.text }]}
            value={qty}
            onChangeText={setQty}
            keyboardType="number-pad"
            placeholder="0"
            placeholderTextColor={theme.muted}
            autoFocus
          />
          <View style={styles.modalButtons}>
            <Pressable style={[styles.modalBtn, { borderColor: theme.border }]} onPress={onClose} disabled={busy}>
              <Text style={{ color: theme.text, fontWeight: "600" }}>Cancel</Text>
            </Pressable>
            <Pressable
              style={[styles.modalBtn, styles.modalBtnPrimary, { backgroundColor: theme.primary }]}
              onPress={onSubmit}
              disabled={busy}
            >
              {busy ? (
                <ActivityIndicator color="#fff" size="small" />
              ) : (
                <Text style={{ color: "#fff", fontWeight: "700" }}>Save count</Text>
              )}
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 },
  title: { fontSize: 16, fontWeight: "800", flexShrink: 1 },
  actionRow: { flexDirection: "row", gap: 8 },
  actionBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    borderRadius: 10,
    paddingVertical: 11,
  },
  search: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10, fontSize: 15 },
  chipRow: { flexDirection: "row", gap: 8 },
  chip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 5 },
  modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "center", padding: 20 },
  modalCard: { borderWidth: 1, borderRadius: 16, padding: 18 },
  inputLabel: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: 14, marginBottom: 4 },
  input: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10, fontSize: 18 },
  modalButtons: { flexDirection: "row", gap: 10, marginTop: 18 },
  modalBtn: { flex: 1, borderWidth: 1, borderRadius: 10, paddingVertical: 12, alignItems: "center" },
  modalBtnPrimary: { borderWidth: 0 },
});
