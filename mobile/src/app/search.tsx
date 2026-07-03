/**
 * Глобальный поиск (как в сайдбаре веба): один запрос — результаты по всем
 * сущностям, тап открывает деталку в приложении.
 */
import Ionicons from "@expo/vector-icons/Ionicons";
import { Href, Stack, useRouter } from "expo-router";
import { useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { Badge } from "@/components/ui";
import { ApiError, GlobalSearchGroup, globalSearch } from "@/lib/api";
import { useTheme } from "@/lib/theme";

/** Веб-URL результата → маршрут приложения. */
function mapUrlToRoute(url: string): Href | null {
  const oid = "[a-f0-9]{24}";
  let m = url.match(new RegExp(`work_order_id=(${oid})`));
  if (m) return { pathname: "/work-order/[id]", params: { id: m[1] } };
  m = url.match(new RegExp(`/units/(${oid})`));
  if (m) return { pathname: "/unit/[id]", params: { id: m[1] } };
  m = url.match(new RegExp(`/customers/(${oid})`));
  if (m) return { pathname: "/customer/[id]", params: { id: m[1] } };
  m = url.match(new RegExp(`/vendors/(${oid})`));
  if (m) return { pathname: "/vendor/[id]", params: { id: m[1] } };
  m = url.match(new RegExp(`/parts/(${oid})`));
  if (m) return { pathname: "/part/[id]", params: { id: m[1] } };
  m = url.match(new RegExp(`open_order=(${oid})`));
  if (m) return { pathname: "/parts-order/[id]", params: { id: m[1] } };
  return null;
}

export default function GlobalSearchScreen() {
  const theme = useTheme();
  const router = useRouter();

  const [q, setQ] = useState("");
  const [groups, setGroups] = useState<GlobalSearchGroup[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [searched, setSearched] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const run = async (text: string) => {
    if (text.trim().length < 2) {
      setGroups([]);
      setSearched(false);
      return;
    }
    setBusy(true);
    try {
      setGroups(await globalSearch(text.trim()));
      setError("");
      setSearched(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Search failed.");
    } finally {
      setBusy(false);
    }
  };

  const onChange = (text: string) => {
    setQ(text);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => run(text), 350);
  };

  return (
    <View style={{ flex: 1, backgroundColor: theme.bg }}>
      <Stack.Screen options={{ title: "Search" }} />
      <View style={styles.searchWrap}>
        <TextInput
          style={[
            styles.search,
            { backgroundColor: theme.surface, borderColor: theme.border, color: theme.text },
          ]}
          value={q}
          onChangeText={onChange}
          placeholder="Search everything…"
          placeholderTextColor={theme.muted}
          autoFocus
          autoCapitalize="none"
        />
      </View>

      {busy ? (
        <ActivityIndicator color={theme.primary} style={{ marginTop: 24 }} />
      ) : error ? (
        <Text style={{ color: theme.danger, textAlign: "center", marginTop: 24 }}>{error}</Text>
      ) : (
        <ScrollView contentContainerStyle={{ paddingBottom: 32 }} keyboardShouldPersistTaps="handled">
          {groups.length === 0 && searched ? (
            <Text style={{ color: theme.muted, textAlign: "center", marginTop: 32 }}>
              Nothing found.
            </Text>
          ) : null}
          {groups.map((g) => (
            <View key={g.category}>
              <Text style={[styles.category, { color: theme.muted }]}>{g.category.toUpperCase()}</Text>
              {g.items.map((item, i) => {
                const route = mapUrlToRoute(item.url || "");
                return (
                  <Pressable
                    key={i}
                    style={[styles.item, { borderBottomColor: theme.border }]}
                    onPress={() => route && router.push(route)}
                    disabled={!route}
                  >
                    <Text
                      style={{ color: route ? theme.text : theme.muted, fontSize: 15, flex: 1 }}
                      numberOfLines={1}
                    >
                      {item.label}
                    </Text>
                    {item.inactive ? <Badge label="Inactive" tone="muted" /> : null}
                    {route ? <Ionicons name="chevron-forward" size={15} color={theme.muted} /> : null}
                  </Pressable>
                );
              })}
            </View>
          ))}
        </ScrollView>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  searchWrap: { padding: 12 },
  search: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 11, fontSize: 15 },
  category: {
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 1,
    paddingHorizontal: 16,
    marginTop: 14,
    marginBottom: 4,
  },
  item: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
  },
});
