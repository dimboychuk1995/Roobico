/**
 * Общие элементы форм: поле с лейблом, свитч, кнопка сохранения.
 */
import React from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  TextInputProps,
  View,
} from "react-native";

import { useTheme } from "@/lib/theme";

export function Field({
  label,
  ...inputProps
}: { label: string } & TextInputProps) {
  const theme = useTheme();
  return (
    <View style={styles.fieldWrap}>
      <Text style={[styles.label, { color: theme.muted }]}>{label.toUpperCase()}</Text>
      <TextInput
        placeholderTextColor={theme.muted}
        {...inputProps}
        style={[
          styles.input,
          { backgroundColor: theme.surface, borderColor: theme.border, color: theme.text },
          inputProps.style,
        ]}
      />
    </View>
  );
}

export function SwitchRow({
  label,
  value,
  onValueChange,
}: {
  label: string;
  value: boolean;
  onValueChange: (v: boolean) => void;
}) {
  const theme = useTheme();
  return (
    <View style={styles.switchRow}>
      <Text style={{ color: theme.text, fontSize: 15 }}>{label}</Text>
      <Switch
        value={value}
        onValueChange={onValueChange}
        trackColor={{ true: theme.primary, false: theme.border }}
        thumbColor="#fff"
      />
    </View>
  );
}

export function SubmitButton({
  title,
  onPress,
  busy,
}: {
  title: string;
  onPress: () => void;
  busy?: boolean;
}) {
  const theme = useTheme();
  return (
    <Pressable
      style={[styles.submit, { backgroundColor: theme.primary, opacity: busy ? 0.7 : 1 }]}
      onPress={onPress}
      disabled={!!busy}
    >
      {busy ? (
        <ActivityIndicator color="#fff" />
      ) : (
        <Text style={styles.submitText}>{title}</Text>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  fieldWrap: { marginTop: 10 },
  label: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginBottom: 4 },
  input: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 11, fontSize: 15 },
  switchRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 14,
  },
  submit: {
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: "center",
    marginTop: 22,
  },
  submitText: { color: "#fff", fontSize: 16, fontWeight: "700" },
});
