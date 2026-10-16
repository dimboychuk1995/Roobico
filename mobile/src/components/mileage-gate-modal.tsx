/**
 * Обязательный пробег: окно показывается механику при открытии WO (и при
 * выборе юнита для нового WO) и не закрывается, пока он не подтвердит
 * текущий одометр — либо не вернётся назад. Без подтверждения ничего не
 * сохраняется (сервер тоже отклоняет сохранения без пробега).
 */
import { useEffect, useState } from "react";
import {
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { useTheme } from "@/lib/theme";

export function MileageGateModal({
  visible,
  unitLabel,
  lastKnown,
  onConfirm,
  onLeave,
}: {
  visible: boolean;
  unitLabel: string;
  lastKnown: string;
  onConfirm: (mileage: string) => void;
  onLeave: () => void;
}) {
  const theme = useTheme();
  const [value, setValue] = useState("");

  useEffect(() => {
    if (visible) setValue(lastKnown || "");
  }, [visible, lastKnown]);

  const digits = value.trim().replace(/,/g, "");
  const valid = /^\d+$/.test(digits);

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onLeave}>
      <KeyboardAvoidingView style={styles.backdrop} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <View style={[styles.card, { backgroundColor: theme.surface, borderColor: theme.border }]}>
          <Text style={[styles.title, { color: theme.text }]}>Current mileage</Text>
          {unitLabel ? <Text style={{ color: theme.text, fontSize: 14 }}>{unitLabel}</Text> : null}
          <Text style={{ color: theme.muted, fontSize: 13 }}>
            {lastKnown ? `Last recorded: ${Number(lastKnown).toLocaleString()}` : "No mileage recorded yet."}
          </Text>
          <TextInput
            value={value}
            onChangeText={setValue}
            keyboardType="number-pad"
            placeholder="e.g. 152300"
            placeholderTextColor={theme.muted}
            autoFocus
            style={[styles.input, { color: theme.text, borderColor: theme.border, backgroundColor: theme.bg }]}
          />
          <Text style={{ color: theme.muted, fontSize: 12 }}>Required before you can work on this order.</Text>
          <View style={styles.row}>
            <Pressable style={[styles.btn, { borderColor: theme.border }]} onPress={onLeave}>
              <Text style={{ color: theme.text, fontWeight: "600" }}>Back</Text>
            </Pressable>
            <Pressable
              style={[styles.btn, styles.btnPrimary, { backgroundColor: valid ? theme.primary : theme.border }]}
              disabled={!valid}
              onPress={() => onConfirm(digits)}
            >
              <Text style={{ color: "#fff", fontWeight: "700" }}>Confirm</Text>
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.45)", justifyContent: "center", padding: 20 },
  card: { borderRadius: 14, borderWidth: 1, padding: 18, gap: 10 },
  title: { fontSize: 18, fontWeight: "700" },
  input: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 14, paddingVertical: 12, fontSize: 20, fontWeight: "600" },
  row: { flexDirection: "row", gap: 10, marginTop: 6 },
  btn: { flex: 1, borderWidth: 1, borderRadius: 10, paddingVertical: 12, alignItems: "center" },
  btnPrimary: { borderWidth: 0 },
});
