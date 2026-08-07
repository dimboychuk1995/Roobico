/**
 * Универсальный списочный экран: поиск с дебаунсом, бесконечная
 * прокрутка, pull-to-refresh, пустые состояния. Все вкладки-списки
 * (Work Orders / Customers / Vendors / Parts) — это он с разными
 * fetchPage/renderItem.
 */
import { useFocusEffect } from "expo-router";
import React, { useCallback, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { ApiError, ListResponse, Pagination } from "@/lib/api";
import { useTheme } from "@/lib/theme";

interface ListScreenProps<T> {
  fetchPage: (q: string, page: number) => Promise<ListResponse<T>>;
  renderItem: (item: T) => React.ReactElement;
  keyExtractor: (item: T) => string;
  searchPlaceholder: string;
  emptyTitle: string;
  emptyHint?: string;
  header?: React.ReactNode;
  /** Дебаунсенное значение поиска — для доп. секций поверх списка (напр., юниты). */
  onQueryChange?: (q: string) => void;
}

export function ListScreen<T>({
  fetchPage,
  renderItem,
  keyExtractor,
  searchPlaceholder,
  emptyTitle,
  emptyHint,
  header,
  onQueryChange,
}: ListScreenProps<T>) {
  const theme = useTheme();
  const insets = useSafeAreaInsets();

  const [query, setQuery] = useState("");
  const [items, setItems] = useState<T[]>([]);
  const [pagination, setPagination] = useState<Pagination | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const requestSeq = useRef(0);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(
    async (q: string, page: number, mode: "replace" | "append") => {
      const seq = ++requestSeq.current;
      try {
        const data = await fetchPage(q, page);
        if (seq !== requestSeq.current) return; // устаревший ответ
        setPagination(data.pagination);
        setItems((prev) => (mode === "append" ? [...prev, ...data.items] : data.items));
        setError("");
      } catch (e) {
        if (seq !== requestSeq.current) return;
        setError(e instanceof ApiError ? e.message : "Failed to load.");
        if (mode === "replace") setItems([]);
      } finally {
        if (seq === requestSeq.current) {
          setLoading(false);
          setLoadingMore(false);
          setRefreshing(false);
        }
      }
    },
    [fetchPage]
  );

  const firstFocus = useRef(true);
  const queryRef = useRef("");
  queryRef.current = query;

  // Первый показ — со спиннером; возврат на экран (после создания/правки
  // записи) — тихое обновление текущей выдачи.
  useFocusEffect(
    useCallback(() => {
      if (firstFocus.current) {
        firstFocus.current = false;
        setLoading(true);
      }
      load(queryRef.current.trim(), 1, "replace");
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])
  );

  const onSearchChange = (text: string) => {
    setQuery(text);
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => {
      setLoading(true);
      onQueryChange?.(text.trim());
      load(text.trim(), 1, "replace");
    }, 300);
  };

  const onRefresh = () => {
    setRefreshing(true);
    load(query.trim(), 1, "replace");
  };

  const onEndReached = () => {
    if (loading || loadingMore || !pagination?.has_next) return;
    setLoadingMore(true);
    load(query.trim(), pagination.page + 1, "append");
  };

  return (
    <View style={[styles.container, { backgroundColor: theme.bg }]}>
      {/* На планшете контент — центрированная колонка, а не лента во всю ширину. */}
      <View style={styles.contentWrap}>
      <View style={styles.searchWrap}>
        <TextInput
          style={[
            styles.search,
            { backgroundColor: theme.surface, borderColor: theme.border, color: theme.text },
          ]}
          placeholder={searchPlaceholder}
          placeholderTextColor={theme.muted}
          value={query}
          onChangeText={onSearchChange}
          autoCapitalize="none"
          autoCorrect={false}
          clearButtonMode="while-editing"
        />
      </View>

      {header}

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={theme.primary} size="large" />
        </View>
      ) : error ? (
        <View style={styles.center}>
          <Text style={[styles.emptyTitle, { color: theme.danger }]}>{error}</Text>
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={keyExtractor}
          renderItem={({ item }) => renderItem(item)}
          contentContainerStyle={{ paddingBottom: insets.bottom + 24, paddingHorizontal: 12 }}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={theme.primary} />
          }
          onEndReachedThreshold={0.4}
          onEndReached={onEndReached}
          ListEmptyComponent={
            <View style={styles.center}>
              <Text style={[styles.emptyTitle, { color: theme.text }]}>{emptyTitle}</Text>
              {emptyHint ? (
                <Text style={[styles.emptyHint, { color: theme.muted }]}>{emptyHint}</Text>
              ) : null}
            </View>
          }
          ListFooterComponent={
            loadingMore ? (
              <ActivityIndicator color={theme.primary} style={{ marginVertical: 16 }} />
            ) : null
          }
        />
      )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  contentWrap: { flex: 1, width: "100%", maxWidth: 760, alignSelf: "center" },
  searchWrap: { paddingHorizontal: 12, paddingTop: 8, paddingBottom: 4 },
  search: {
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 15,
  },
  center: { alignItems: "center", justifyContent: "center", paddingVertical: 48, gap: 6 },
  emptyTitle: { fontSize: 16, fontWeight: "600" },
  emptyHint: { fontSize: 13, textAlign: "center", paddingHorizontal: 24 },
});
