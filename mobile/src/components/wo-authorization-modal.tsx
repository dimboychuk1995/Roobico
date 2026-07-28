/**
 * Отправка WO/работы на утверждение клиенту (email с Approve/Decline).
 * Используется менеджерским и механик-видом WO.
 */
import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { useToast } from "@/context/toast";
import { ApiError, sendWoAuthorization } from "@/lib/api";
import { useTheme } from "@/lib/theme";

export function WoAuthorizationModal({
  visible,
  onClose,
  woId,
  defaultEmail,
  scope = "work_order",
  laborIndex,
  jobLabel,
}: {
  visible: boolean;
  onClose: () => void;
  woId: string;
  defaultEmail: string;
  scope?: "work_order" | "labor";
  laborIndex?: number;
  jobLabel?: string;
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
      await sendWoAuthorization(woId, target, scope, laborIndex);
      toast.show("Approval request sent.", "success");
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
          <Text style={[styles.modalTitle, { color: theme.text }]}>
            {scope === "labor" ? "Send job for approval" : "Send for approval"}
          </Text>
          <Text style={{ color: theme.muted, fontSize: 13 }}>
            {scope === "labor"
              ? `The customer will receive an email about "${jobLabel || "this job"}" with Approve / Decline buttons.`
              : "The customer will receive an email with the full work order and Approve / Decline buttons."}
          </Text>

          <Text style={[styles.inputLabel, { color: theme.muted }]}>CUSTOMER EMAIL</Text>
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

const styles = StyleSheet.create({
  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.45)",
    justifyContent: "center",
    padding: 22,
  },
  modalCard: {
    borderWidth: 1,
    borderRadius: 16,
    padding: 18,
    gap: 8,
  },
  modalTitle: { fontSize: 17, fontWeight: "800" },
  inputLabel: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: 8 },
  input: {
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 15,
  },
  modalButtons: { flexDirection: "row", justifyContent: "flex-end", gap: 10, marginTop: 14 },
  modalBtn: {
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 18,
    paddingVertical: 10,
    alignItems: "center",
    justifyContent: "center",
    minWidth: 92,
  },
  modalBtnPrimary: { borderWidth: 0 },
});
