import Ionicons from "@expo/vector-icons/Ionicons";
import { Redirect, Stack, useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";
import { useCallback, useEffect, useState } from "react";
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

import { AttachmentsBlock } from "@/components/attachments-block";
import { Badge, KV, RowCard } from "@/components/ui";
import { WoAuthorizationModal } from "@/components/wo-authorization-modal";
import { useHasPermission, useIsMechanic } from "@/context/auth";
import { useToast } from "@/context/toast";
import {
  ApiError,
  WoPayments,
  WorkOrderDetails,
  deleteWoPayment,
  deleteWorkOrder,
  fetchWoPayments,
  fetchWorkOrderDetails,
  money,
  recordWoPayment,
  sendWorkOrderEmail,
  setWorkOrderConfirmed,
  setWorkOrderStatus,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

const PAYMENT_METHODS = ["cash", "check", "card", "ach", "other"];

export default function WorkOrderDetailsScreen() {
  const isMechanic = useIsMechanic();
  const { id } = useLocalSearchParams<{ id: string }>();
  // Механик: всегда edit-вид — WO-форма с таймерами, фото и утверждением,
  // отдельного read-only экрана деталей у механика нет.
  if (isMechanic) {
    return <Redirect href={{ pathname: "/work-order-form", params: { id: String(id || "") } }} />;
  }
  return <ManagerWoScreen />;
}

function statusBadgeFor(status: string) {
  return status === "paid"
    ? <Badge label="Paid" tone="success" />
    : status === "in_progress"
      ? <Badge label="In Progress" tone="info" />
      : <Badge label="Open" tone="warning" />;
}

function ManagerWoScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const theme = useTheme();
  const toast = useToast();
  const router = useRouter();

  const [wo, setWo] = useState<WorkOrderDetails | null>(null);
  const [payments, setPayments] = useState<WoPayments | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [payModalOpen, setPayModalOpen] = useState(false);
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [emailModalOpen, setEmailModalOpen] = useState(false);

  // Реальный ключ платежей — work_orders.manage_payments (record_payment не
  // существует в каталоге; старый фолбэк на create давал платежи всем).
  const canPay = useHasPermission("work_orders.manage_payments");
  const canEdit = useHasPermission("work_orders.create");

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const [woData, payData] = await Promise.all([
        fetchWorkOrderDetails(id),
        fetchWoPayments(id).catch(() => null),
      ]);
      setWo(woData);
      setPayments(payData);
      setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load work order.");
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

  const onDeleteWo = () => {
    Alert.alert(
      "Delete work order",
      "Parts will be returned to inventory and all payments removed. This cannot be undone.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete",
          style: "destructive",
          onPress: async () => {
            try {
              await deleteWorkOrder(id!);
              toast.show("Work order deleted.", "success");
              router.back();
            } catch (e) {
              toast.show(e instanceof ApiError ? e.message : "Delete failed.", "error");
            }
          },
        },
      ]
    );
  };

  const onMarkUnpaid = async () => {
    try {
      await setWorkOrderStatus(id!, "open");
      toast.show("Marked as unpaid.", "success");
      load();
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Failed.", "error");
    }
  };

  // Подтверждение работы механика: подтверждённый WO для механика закрыт,
  // отмена подтверждения снова открывает его для правок.
  const onToggleConfirmed = async () => {
    try {
      const res = await setWorkOrderConfirmed(id!, !wo?.manager_confirmed);
      toast.show(
        res.manager_confirmed
          ? "Work order confirmed — locked for mechanics."
          : "Confirmation cancelled — mechanics can edit again.",
        "success"
      );
      load();
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Failed.", "error");
    }
  };

  const onDeletePayment = (paymentId: string) => {
    Alert.alert("Delete payment", "Delete this payment? The balance will be restored.", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete",
        style: "destructive",
        onPress: async () => {
          try {
            await deleteWoPayment(paymentId);
            toast.show("Payment deleted", "success");
            load();
          } catch (e) {
            toast.show(e instanceof ApiError ? e.message : "Failed to delete payment.", "error");
          }
        },
      },
    ]);
  };

  if (loading) {
    return (
      <View style={[styles.center, { backgroundColor: theme.bg }]}>
        <Stack.Screen options={{ title: "Work Order" }} />
        <ActivityIndicator color={theme.primary} size="large" />
      </View>
    );
  }

  if (error || !wo) {
    return (
      <View style={[styles.center, { backgroundColor: theme.bg }]}>
        <Stack.Screen options={{ title: "Work Order" }} />
        <Text style={{ color: theme.danger, textAlign: "center", padding: 24 }}>
          {error || "Work order not found."}
        </Text>
      </View>
    );
  }

  const t = wo.t;
  const balance = payments?.remaining_balance ?? t.remaining_balance;
  const statusBadge = wo.status === "paid"
    ? <Badge label="Paid" tone="success" />
    : wo.status === "in_progress"
      ? <Badge label="In Progress" tone="info" />
      : <Badge label="Open" tone="warning" />;

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
          title: `WO #${wo.wo_number}`,
          headerRight: () =>
            canEdit && wo.status !== "paid" ? (
              <Pressable
                onPress={() => router.push({ pathname: "/work-order-form", params: { id: wo.id } })}
                hitSlop={8}
                style={{ paddingHorizontal: 8 }}
              >
                <Ionicons name="create-outline" size={22} color={theme.primary} />
              </Pressable>
            ) : null,
        }}
      />

      {/* Шапка */}
      <RowCard>
        <View style={styles.headerRow}>
          <Text style={[styles.woNumber, { color: theme.text }]}>WO #{wo.wo_number}</Text>
          <View style={styles.badgeRow}>
            {wo.manager_confirmed ? <Badge label="Confirmed" tone="success" /> : null}
            {statusBadge}
          </View>
        </View>
        <KV label="Customer" value={wo.cust_name} />
        <KV label="Phone" value={wo.customer_phone} />
        <KV label="Unit" value={wo.unit_label} />
        <KV label="VIN" value={wo.unit_vin} />
        <KV label="Mileage" value={wo.unit_mileage} />
        <KV label="Date" value={wo.wo_date_label} />
      </RowCard>

      {/* Лейборы */}
      <Text style={[styles.sectionTitle, { color: theme.muted }]}>LABORS</Text>
      {wo.labors.map((labor, idx) => (
        <RowCard key={idx}>
          <View style={styles.headerRow}>
            <Text style={[styles.laborTitle, { color: theme.text }]} numberOfLines={2}>
              {idx + 1}. {labor.labor_desc}
            </Text>
            <Text style={[styles.laborTotal, { color: theme.text }]}>
              {money(labor.block_total)}
            </Text>
          </View>
          {labor.issue_description ? (
            <Text style={[styles.issue, { color: theme.muted }]}>
              Issue: {labor.issue_description}
            </Text>
          ) : null}
          <KV
            label="Labor"
            value={`${labor.hours ? labor.hours + " h · " : ""}${money(labor.labor_total)}`}
          />
          {labor.tracked_seconds ? (
            <KV label="Tracked" value={labor.tracked_label || ""} />
          ) : null}
          {labor.parts.length > 0 ? (
            <View style={[styles.partsBox, { borderColor: theme.border }]}>
              {labor.parts.map((p, pIdx) => (
                <View key={pIdx} style={styles.partRow}>
                  <Text style={[styles.partLabel, { color: theme.text }]} numberOfLines={1}>
                    {p.label || p.part_number}
                  </Text>
                  <Text style={[styles.partQty, { color: theme.muted }]}>
                    ×{p.qty}
                  </Text>
                  <Text style={[styles.partTotal, { color: theme.text }]}>{money(p.total)}</Text>
                </View>
              ))}
            </View>
          ) : null}
          {labor.misc_items.map((m, mIdx) => (
            <KV key={mIdx} label={m.description} value={money(m.total)} />
          ))}
        </RowCard>
      ))}

      {/* Тоталы */}
      <Text style={[styles.sectionTitle, { color: theme.muted }]}>TOTALS</Text>
      <RowCard>
        <KV label="Labor" value={money(t.labor_total)} />
        <KV label="Parts" value={money(t.parts_total)} />
        {t.shop_supply_total > 0 ? <KV label="Shop supply" value={money(t.shop_supply_total)} /> : null}
        {t.misc_total > 0 ? <KV label="Misc" value={money(t.misc_total)} /> : null}
        {t.core_total > 0 ? <KV label="Cores" value={money(t.core_total)} /> : null}
        {t.sales_tax_total > 0 ? <KV label="Tax" value={money(t.sales_tax_total)} /> : null}
        <View style={[styles.grandRow, { borderTopColor: theme.border }]}>
          <Text style={[styles.grandLabel, { color: theme.text }]}>Grand total</Text>
          <Text style={[styles.grandValue, { color: theme.text }]}>{money(t.grand_total)}</Text>
        </View>
        <KV label="Paid" value={money(payments?.paid_amount ?? t.paid_total)} />
        <View style={styles.headerRow}>
          <Text style={{ color: theme.muted, fontSize: 13 }}>Balance</Text>
          <Text
            style={{
              color: balance > 0 ? theme.warning : theme.primary,
              fontSize: 15,
              fontWeight: "800",
            }}
          >
            {money(balance)}
          </Text>
        </View>
      </RowCard>

      {/* Платежи */}
      <Text style={[styles.sectionTitle, { color: theme.muted }]}>PAYMENTS</Text>
      {(payments?.payments || []).length === 0 ? (
        <RowCard>
          <Text style={{ color: theme.muted, fontSize: 13 }}>No payments recorded yet.</Text>
        </RowCard>
      ) : (
        (payments?.payments || []).map((p) => (
          <RowCard key={p.id}>
            <View style={styles.headerRow}>
              <Text style={{ color: theme.text, fontSize: 15, fontWeight: "700" }}>
                {money(p.amount)}
              </Text>
              {canPay ? (
                <Pressable onPress={() => onDeletePayment(p.id)} hitSlop={8}>
                  <Ionicons name="trash-outline" size={18} color={theme.danger} />
                </Pressable>
              ) : null}
            </View>
            <KV label="Method" value={p.payment_method} />
            <KV label="Date" value={p.payment_date_label} />
            {p.notes ? <KV label="Notes" value={p.notes} /> : null}
          </RowCard>
        ))
      )}

      {canPay && balance > 0 && wo.status !== "paid" ? (
        <Pressable
          style={[styles.payButton, { backgroundColor: theme.primary }]}
          onPress={() => setPayModalOpen(true)}
        >
          <Ionicons name="cash-outline" size={18} color="#fff" />
          <Text style={styles.payButtonText}>Record payment</Text>
        </Pressable>
      ) : null}

      <AttachmentsBlock entityType="work_order" entityId={wo.id} />

      {canPay && wo.status !== "paid" ? (
        <Pressable
          style={[styles.secondaryBtn, { borderColor: theme.border, backgroundColor: theme.surface }]}
          onPress={() => setAuthModalOpen(true)}
        >
          <Ionicons name="send-outline" size={16} color={theme.primary} />
          <Text style={{ color: theme.primary, fontWeight: "600" }}>Send for authorization</Text>
        </Pressable>
      ) : null}

      <Pressable
        style={[styles.secondaryBtn, { borderColor: theme.border, backgroundColor: theme.surface }]}
        onPress={onToggleConfirmed}
      >
        <Ionicons
          name={wo.manager_confirmed ? "lock-open-outline" : "checkmark-circle-outline"}
          size={16}
          color={wo.manager_confirmed ? theme.warning : theme.primary}
        />
        <Text
          style={{ color: wo.manager_confirmed ? theme.warning : theme.primary, fontWeight: "600" }}
        >
          {wo.manager_confirmed ? "Cancel confirmation" : "Confirm work order"}
        </Text>
      </Pressable>

      <Pressable
        style={[styles.secondaryBtn, { borderColor: theme.border, backgroundColor: theme.surface }]}
        onPress={() => setEmailModalOpen(true)}
      >
        <Ionicons name="mail-outline" size={16} color={theme.primary} />
        <Text style={{ color: theme.primary, fontWeight: "600" }}>Email work order (PDF)</Text>
      </Pressable>

      {canPay && wo.status === "paid" ? (
        <Pressable
          style={[styles.secondaryBtn, { borderColor: theme.border, backgroundColor: theme.surface }]}
          onPress={onMarkUnpaid}
        >
          <Ionicons name="arrow-undo-outline" size={16} color={theme.warning} />
          <Text style={{ color: theme.warning, fontWeight: "600" }}>Mark unpaid</Text>
        </Pressable>
      ) : null}

      {canPay ? (
        <Pressable
          style={[styles.secondaryBtn, { borderColor: theme.border, backgroundColor: theme.surface }]}
          onPress={onDeleteWo}
        >
          <Ionicons name="trash-outline" size={16} color={theme.danger} />
          <Text style={{ color: theme.danger, fontWeight: "600" }}>Delete work order</Text>
        </Pressable>
      ) : null}

      <RecordPaymentModal
        visible={payModalOpen}
        onClose={() => setPayModalOpen(false)}
        woId={wo.id}
        defaultAmount={balance}
        onSaved={() => {
          setPayModalOpen(false);
          load();
        }}
      />

      <WoAuthorizationModal
        visible={authModalOpen}
        onClose={() => setAuthModalOpen(false)}
        woId={wo.id}
        defaultEmail={wo.customer_email || ""}
      />

      <EmailWoModal
        visible={emailModalOpen}
        onClose={() => setEmailModalOpen(false)}
        woId={wo.id}
        defaultEmail={wo.customer_email || ""}
      />
    </ScrollView>
  );
}

function EmailWoModal({
  visible,
  onClose,
  woId,
  defaultEmail,
}: {
  visible: boolean;
  onClose: () => void;
  woId: string;
  defaultEmail: string;
}) {
  const theme = useTheme();
  const toast = useToast();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (visible) setEmail(defaultEmail);
  }, [visible, defaultEmail]);

  const onSend = async () => {
    const target = email.trim().toLowerCase();
    if (!target || !target.includes("@")) {
      toast.show("Enter a valid email address.", "error");
      return;
    }
    setBusy(true);
    try {
      await sendWorkOrderEmail(woId, target);
      toast.show("Work order emailed.", "success");
      onClose();
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Failed to send.", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <KeyboardAvoidingView
        style={styles.modalBackdrop}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <View style={[styles.modalCard, { backgroundColor: theme.surface, borderColor: theme.border }]}>
          <Text style={[styles.modalTitle, { color: theme.text }]}>Email work order</Text>
          <Text style={{ color: theme.muted, fontSize: 13 }}>
            A PDF copy of this work order will be sent by email.
          </Text>

          <Text style={[styles.inputLabel, { color: theme.muted }]}>EMAIL</Text>
          <TextInput
            style={[
              styles.input,
              { backgroundColor: theme.surfaceSoft, borderColor: theme.border, color: theme.text },
            ]}
            value={email}
            onChangeText={setEmail}
            keyboardType="email-address"
            autoCapitalize="none"
            placeholder="customer@company.com"
            placeholderTextColor={theme.muted}
          />

          <View style={styles.modalButtons}>
            <Pressable style={[styles.modalBtn, { borderColor: theme.border }]} onPress={onClose} disabled={busy}>
              <Text style={{ color: theme.text, fontWeight: "600" }}>Cancel</Text>
            </Pressable>
            <Pressable
              style={[styles.modalBtn, styles.modalBtnPrimary, { backgroundColor: theme.primary }]}
              onPress={onSend}
              disabled={busy}
            >
              {busy ? (
                <ActivityIndicator color="#fff" size="small" />
              ) : (
                <Text style={{ color: "#fff", fontWeight: "700" }}>Send</Text>
              )}
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

function RecordPaymentModal({
  visible,
  onClose,
  woId,
  defaultAmount,
  onSaved,
}: {
  visible: boolean;
  onClose: () => void;
  woId: string;
  defaultAmount: number;
  onSaved: () => void;
}) {
  const theme = useTheme();
  const toast = useToast();
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState("cash");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (visible) {
      setAmount(defaultAmount > 0 ? defaultAmount.toFixed(2) : "");
      setMethod("cash");
      setNotes("");
    }
  }, [visible, defaultAmount]);

  const onSubmit = async () => {
    const num = parseFloat(amount.replace(",", "."));
    if (!Number.isFinite(num) || num <= 0) {
      toast.show("Enter a valid amount.", "error");
      return;
    }
    setBusy(true);
    try {
      const res = await recordWoPayment(woId, num, method, notes.trim());
      toast.show(
        res.status === "paid" ? "Payment recorded — work order is paid." : "Payment recorded.",
        "success"
      );
      onSaved();
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Failed to record payment.", "error");
    } finally {
      setBusy(false);
    }
  };

  const inputStyle = [
    styles.input,
    { backgroundColor: theme.surfaceSoft, borderColor: theme.border, color: theme.text },
  ];

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <KeyboardAvoidingView
        style={styles.modalBackdrop}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <View style={[styles.modalCard, { backgroundColor: theme.surface, borderColor: theme.border }]}>
          <Text style={[styles.modalTitle, { color: theme.text }]}>Record payment</Text>

          <Text style={[styles.inputLabel, { color: theme.muted }]}>AMOUNT</Text>
          <TextInput
            style={inputStyle}
            value={amount}
            onChangeText={setAmount}
            keyboardType="decimal-pad"
            placeholder="0.00"
            placeholderTextColor={theme.muted}
          />

          <Text style={[styles.inputLabel, { color: theme.muted }]}>METHOD</Text>
          <View style={styles.methodRow}>
            {PAYMENT_METHODS.map((m) => (
              <Pressable
                key={m}
                onPress={() => setMethod(m)}
                style={[
                  styles.methodChip,
                  {
                    backgroundColor: method === m ? theme.primary : theme.surfaceSoft,
                    borderColor: method === m ? theme.primary : theme.border,
                  },
                ]}
              >
                <Text
                  style={{
                    color: method === m ? "#fff" : theme.text,
                    fontSize: 13,
                    fontWeight: "600",
                  }}
                >
                  {m}
                </Text>
              </Pressable>
            ))}
          </View>

          <Text style={[styles.inputLabel, { color: theme.muted }]}>NOTES (OPTIONAL)</Text>
          <TextInput
            style={inputStyle}
            value={notes}
            onChangeText={setNotes}
            placeholder="Check #, reference…"
            placeholderTextColor={theme.muted}
          />

          <View style={styles.modalButtons}>
            <Pressable
              style={[styles.modalBtn, { borderColor: theme.border }]}
              onPress={onClose}
              disabled={busy}
            >
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
                <Text style={{ color: "#fff", fontWeight: "700" }}>Save</Text>
              )}
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  container: { padding: 12, paddingBottom: 40, gap: 4 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 },
  badgeRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  woNumber: { fontSize: 17, fontWeight: "800" },
  sectionTitle: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: 14, marginBottom: 2 },
  laborTitle: { fontSize: 14, fontWeight: "700", flex: 1 },
  laborTotal: { fontSize: 14, fontWeight: "700" },
  issue: { fontSize: 12, fontStyle: "italic" },
  partsBox: { borderTopWidth: 1, marginTop: 6, paddingTop: 6, gap: 4 },
  partRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  partLabel: { flex: 1, fontSize: 13 },
  partQty: { fontSize: 13, width: 36, textAlign: "right" },
  partTotal: { fontSize: 13, fontWeight: "600", width: 76, textAlign: "right" },
  grandRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    borderTopWidth: 1,
    marginTop: 6,
    paddingTop: 8,
  },
  grandLabel: { fontSize: 15, fontWeight: "800" },
  grandValue: { fontSize: 15, fontWeight: "800" },
  payButton: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    borderRadius: 12,
    paddingVertical: 14,
    marginTop: 16,
  },
  payButtonText: { color: "#fff", fontSize: 15, fontWeight: "700" },
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
  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.5)",
    justifyContent: "center",
    padding: 20,
  },
  modalCard: { borderWidth: 1, borderRadius: 16, padding: 18 },
  modalTitle: { fontSize: 17, fontWeight: "800", marginBottom: 8 },
  inputLabel: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: 12, marginBottom: 4 },
  input: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10, fontSize: 15 },
  methodRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  methodChip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 6 },
  modalButtons: { flexDirection: "row", gap: 10, marginTop: 18 },
  modalBtn: {
    flex: 1,
    borderWidth: 1,
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: "center",
  },
  modalBtnPrimary: { borderWidth: 0 },
});
