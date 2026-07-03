import Ionicons from "@expo/vector-icons/Ionicons";
import { Stack, useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";
import { useCallback, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { Badge, KV, RowCard } from "@/components/ui";
import { useToast } from "@/context/toast";
import {
  ApiError,
  PartsOrderDetail,
  fetchPartsOrder,
  money,
  payPartsOrder,
  receivePartsOrder,
  unreceivePartsOrder,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

export default function PartsOrderDetailsScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const theme = useTheme();
  const toast = useToast();
  const router = useRouter();

  const [order, setOrder] = useState<PartsOrderDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [payOpen, setPayOpen] = useState(false);
  const [receiveOpen, setReceiveOpen] = useState(false);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      setOrder(await fetchPartsOrder(id));
      setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load order.");
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

  const onUnreceive = () => {
    Alert.alert("Unreceive order", "Parts will be removed from inventory. Continue?", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Unreceive",
        style: "destructive",
        onPress: async () => {
          setBusy(true);
          try {
            await unreceivePartsOrder(id!);
            toast.show("Order unreceived.", "success");
            load();
          } catch (e) {
            toast.show(e instanceof ApiError ? e.message : "Failed.", "error");
          } finally {
            setBusy(false);
          }
        },
      },
    ]);
  };

  if (loading) {
    return (
      <View style={[styles.center, { backgroundColor: theme.bg }]}>
        <Stack.Screen options={{ title: "Parts Order" }} />
        <ActivityIndicator color={theme.primary} size="large" />
      </View>
    );
  }

  if (error || !order) {
    return (
      <View style={[styles.center, { backgroundColor: theme.bg }]}>
        <Stack.Screen options={{ title: "Parts Order" }} />
        <Text style={{ color: theme.danger, textAlign: "center", padding: 24 }}>
          {error || "Order not found."}
        </Text>
      </View>
    );
  }

  const summary = order.payment_summary;
  const received = order.status === "received";
  const itemsTotal = order.items.reduce((s, i) => s + i.quantity * i.price, 0);

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
      <Stack.Screen options={{ title: "Parts Order" }} />

      <RowCard>
        <View style={styles.headerRow}>
          <Text style={[styles.title, { color: theme.text }]}>Order</Text>
          {received ? <Badge label="Received" tone="success" /> : <Badge label={order.status || "ordered"} tone="warning" />}
        </View>
        <KV label="Vendor bill" value={order.vendor_bill} />
        <KV label="Order date" value={order.order_date} />
        {order.received_at ? <KV label="Received at" value={order.received_at.slice(0, 10)} /> : null}
        <KV label="Payment" value={summary.payment_status} />
      </RowCard>

      <Text style={[styles.sectionTitle, { color: theme.muted }]}>ITEMS ({order.items.length})</Text>
      {order.items.map((it, idx) => (
        <RowCard key={idx}>
          <View style={styles.headerRow}>
            <Text style={{ color: theme.text, fontSize: 14, fontWeight: "700", flex: 1 }} numberOfLines={1}>
              {it.part_number || it.description || "Part"}
            </Text>
            <Text style={{ color: theme.text, fontSize: 14, fontWeight: "700" }}>
              {money(it.quantity * it.price)}
            </Text>
          </View>
          <Text style={{ color: theme.muted, fontSize: 13 }}>
            ×{it.quantity} @ {money(it.price)}
            {it.core_charge > 0 ? ` · core ${money(it.core_charge)}` : ""}
          </Text>
        </RowCard>
      ))}

      {order.non_inventory_amounts.length > 0 ? (
        <>
          <Text style={[styles.sectionTitle, { color: theme.muted }]}>OTHER CHARGES</Text>
          <RowCard>
            {order.non_inventory_amounts.map((x, i) => (
              <KV key={i} label={x.description || x.type} value={money(x.amount)} />
            ))}
          </RowCard>
        </>
      ) : null}

      <Text style={[styles.sectionTitle, { color: theme.muted }]}>TOTALS</Text>
      <RowCard>
        <KV label="Items" value={money(itemsTotal)} />
        <KV label="Total" value={money(summary.total_amount)} />
        <KV label="Paid" value={money(summary.paid_amount)} />
        <View style={styles.headerRow}>
          <Text style={{ color: theme.muted, fontSize: 13 }}>Balance</Text>
          <Text
            style={{
              color: summary.remaining_balance > 0 ? theme.warning : theme.primary,
              fontWeight: "800",
              fontSize: 15,
            }}
          >
            {money(summary.remaining_balance)}
          </Text>
        </View>
      </RowCard>

      {!received ? (
        <Pressable
          style={[styles.primaryBtn, { backgroundColor: theme.primary, opacity: busy ? 0.7 : 1 }]}
          onPress={() => setReceiveOpen(true)}
          disabled={busy}
        >
          <Ionicons name="checkmark-circle-outline" size={18} color="#fff" />
          <Text style={styles.primaryBtnText}>Receive order</Text>
        </Pressable>
      ) : (
        <Pressable
          style={[styles.secondaryBtn, { borderColor: theme.border, backgroundColor: theme.surface }]}
          onPress={onUnreceive}
          disabled={busy}
        >
          <Ionicons name="arrow-undo-outline" size={16} color={theme.warning} />
          <Text style={{ color: theme.warning, fontWeight: "600" }}>Unreceive order</Text>
        </Pressable>
      )}

      {summary.remaining_balance > 0 ? (
        <Pressable
          style={[styles.secondaryBtn, { borderColor: theme.border, backgroundColor: theme.surface }]}
          onPress={() => setPayOpen(true)}
        >
          <Ionicons name="cash-outline" size={16} color={theme.primary} />
          <Text style={{ color: theme.primary, fontWeight: "600" }}>Record payment</Text>
        </Pressable>
      ) : null}

      <ReceiveModal
        visible={receiveOpen}
        defaultBill={order.vendor_bill}
        onClose={() => setReceiveOpen(false)}
        onDone={() => {
          setReceiveOpen(false);
          load();
        }}
        orderId={order.id}
      />
      <PayOrderModal
        visible={payOpen}
        defaultAmount={summary.remaining_balance}
        onClose={() => setPayOpen(false)}
        onDone={() => {
          setPayOpen(false);
          load();
        }}
        orderId={order.id}
      />
    </ScrollView>
  );
}

function ReceiveModal({
  visible,
  defaultBill,
  orderId,
  onClose,
  onDone,
}: {
  visible: boolean;
  defaultBill: string;
  orderId: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const theme = useTheme();
  const toast = useToast();
  const [bill, setBill] = useState(defaultBill);
  const [busy, setBusy] = useState(false);

  const onSubmit = async () => {
    setBusy(true);
    try {
      await receivePartsOrder(orderId, bill.trim());
      toast.show("Order received — parts added to stock.", "success");
      onDone();
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Receive failed.", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SimpleModal visible={visible} onClose={onClose} title="Receive order">
      <Text style={{ color: theme.muted, fontSize: 13 }}>
        Parts will be added to inventory.
      </Text>
      <Text style={[styles.inputLabel, { color: theme.muted }]}>VENDOR BILL # (OPTIONAL)</Text>
      <TextInput
        style={[styles.input, { backgroundColor: theme.surfaceSoft, borderColor: theme.border, color: theme.text }]}
        value={bill}
        onChangeText={setBill}
        placeholder="INV-1234"
        placeholderTextColor={theme.muted}
      />
      <ModalButtons onClose={onClose} onSubmit={onSubmit} busy={busy} submitLabel="Receive" />
    </SimpleModal>
  );
}

function PayOrderModal({
  visible,
  defaultAmount,
  orderId,
  onClose,
  onDone,
}: {
  visible: boolean;
  defaultAmount: number;
  orderId: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const theme = useTheme();
  const toast = useToast();
  const [amount, setAmount] = useState(defaultAmount > 0 ? defaultAmount.toFixed(2) : "");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);

  const onSubmit = async () => {
    const num = parseFloat(amount.replace(",", "."));
    if (!Number.isFinite(num) || num <= 0) {
      toast.show("Enter a valid amount.", "error");
      return;
    }
    setBusy(true);
    try {
      await payPartsOrder(orderId, num, "cash", notes.trim());
      toast.show("Payment recorded.", "success");
      onDone();
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Payment failed.", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SimpleModal visible={visible} onClose={onClose} title="Record payment">
      <Text style={[styles.inputLabel, { color: theme.muted }]}>AMOUNT</Text>
      <TextInput
        style={[styles.input, { backgroundColor: theme.surfaceSoft, borderColor: theme.border, color: theme.text }]}
        value={amount}
        onChangeText={setAmount}
        keyboardType="decimal-pad"
        placeholder="0.00"
        placeholderTextColor={theme.muted}
      />
      <Text style={[styles.inputLabel, { color: theme.muted }]}>NOTES (OPTIONAL)</Text>
      <TextInput
        style={[styles.input, { backgroundColor: theme.surfaceSoft, borderColor: theme.border, color: theme.text }]}
        value={notes}
        onChangeText={setNotes}
        placeholder="Check #, reference…"
        placeholderTextColor={theme.muted}
      />
      <ModalButtons onClose={onClose} onSubmit={onSubmit} busy={busy} submitLabel="Save" />
    </SimpleModal>
  );
}

function SimpleModal({
  visible,
  onClose,
  title,
  children,
}: {
  visible: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}) {
  const theme = useTheme();
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <KeyboardAvoidingView
        style={styles.modalBackdrop}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <View style={[styles.modalCard, { backgroundColor: theme.surface, borderColor: theme.border }]}>
          <Text style={[styles.modalTitle, { color: theme.text }]}>{title}</Text>
          {children}
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

function ModalButtons({
  onClose,
  onSubmit,
  busy,
  submitLabel,
}: {
  onClose: () => void;
  onSubmit: () => void;
  busy: boolean;
  submitLabel: string;
}) {
  const theme = useTheme();
  return (
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
          <Text style={{ color: "#fff", fontWeight: "700" }}>{submitLabel}</Text>
        )}
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { padding: 12, paddingBottom: 40 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 },
  title: { fontSize: 17, fontWeight: "800" },
  sectionTitle: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: 14, marginBottom: 2 },
  primaryBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    borderRadius: 12,
    paddingVertical: 14,
    marginTop: 16,
  },
  primaryBtnText: { color: "#fff", fontSize: 15, fontWeight: "700" },
  secondaryBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    borderWidth: 1,
    borderRadius: 12,
    paddingVertical: 13,
    marginTop: 10,
  },
  modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "center", padding: 20 },
  modalCard: { borderWidth: 1, borderRadius: 16, padding: 18 },
  modalTitle: { fontSize: 17, fontWeight: "800", marginBottom: 8 },
  inputLabel: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: 12, marginBottom: 4 },
  input: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10, fontSize: 15 },
  modalButtons: { flexDirection: "row", gap: 10, marginTop: 18 },
  modalBtn: { flex: 1, borderWidth: 1, borderRadius: 10, paddingVertical: 12, alignItems: "center" },
  modalBtnPrimary: { borderWidth: 0 },
});
