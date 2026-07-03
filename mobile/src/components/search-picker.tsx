/**
 * Общая полноэкранная модалка «поиск → выбор» (клиенты, вендоры,
 * запчасти, пресеты…).
 */
import Ionicons from "@expo/vector-icons/Ionicons";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { useTheme } from "@/lib/theme";

export function SearchPickerModal<T>({
  visible,
  onClose,
  title,
  placeholder,
  search,
  renderLabel,
  onPick,
}: {
  visible: boolean;
  onClose: () => void;
  title: string;
  placeholder: string;
  search: (q: string) => Promise<T[]>;
  renderLabel: (item: T) => string;
  onPick: (item: T) => void;
}) {
  const theme = useTheme();
  const [q, setQ] = useState("");
  const [items, setItems] = useState<T[]>([]);
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const run = useCallback(
    (text: string) => {
      setBusy(true);
      search(text)
        .then(setItems)
        .catch(() => setItems([]))
        .finally(() => setBusy(false));
    },
    [search]
  );

  useEffect(() => {
    if (visible) {
      setQ("");
      run("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  const onChange = (text: string) => {
    setQ(text);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => run(text.trim()), 300);
  };

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: theme.bg, paddingTop: 56 }}>
        <View style={styles.header}>
          <Text style={{ color: theme.text, fontSize: 17, fontWeight: "800" }}>{title}</Text>
          <Pressable onPress={onClose} hitSlop={8}>
            <Ionicons name="close" size={24} color={theme.muted} />
          </Pressable>
        </View>
        <TextInput
          style={[
            styles.search,
            { backgroundColor: theme.surface, borderColor: theme.border, color: theme.text },
          ]}
          value={q}
          onChangeText={onChange}
          placeholder={placeholder}
          placeholderTextColor={theme.muted}
          autoFocus
        />
        {busy ? (
          <ActivityIndicator color={theme.primary} style={{ marginTop: 24 }} />
        ) : (
          <FlatList
            data={items}
            keyExtractor={(_, i) => String(i)}
            keyboardShouldPersistTaps="handled"
            renderItem={({ item }) => (
              <Pressable
                style={[styles.item, { borderBottomColor: theme.border }]}
                onPress={() => onPick(item)}
              >
                <Text style={{ color: theme.text, fontSize: 15 }}>{renderLabel(item)}</Text>
              </Pressable>
            )}
            ListEmptyComponent={
              <Text style={{ color: theme.muted, textAlign: "center", marginTop: 32 }}>
                Nothing found.
              </Text>
            }
          />
        )}
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: 16,
    paddingBottom: 10,
  },
  search: {
    borderWidth: 1,
    borderRadius: 10,
    marginHorizontal: 16,
    marginBottom: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 15,
  },
  item: { paddingHorizontal: 16, paddingVertical: 14, borderBottomWidth: 1 },
});
