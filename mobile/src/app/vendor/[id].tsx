import Ionicons from "@expo/vector-icons/Ionicons";
import { Stack, useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Linking,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { Badge, KV, RowCard } from "@/components/ui";
import {
  ApiError,
  VendorFull,
  VendorOrderRow,
  VendorOrdersSummary,
  fetchVendorCredentials,
  fetchVendorFull,
  fetchVendorOrders,
  money,
} from "@/lib/api";
import { useHasPermission } from "@/context/auth";
import { useToast } from "@/context/toast";
import { useTheme } from "@/lib/theme";
import { websiteHref, websiteLabel } from "@/lib/website";

const PASSWORD_MASK = "••••••••";
// Показанный пароль прячется сам через 30 секунд (и сразу — при уходе с экрана).
const PASSWORD_VISIBLE_MS = 30000;

export default function VendorDetailsScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const theme = useTheme();
  const router = useRouter();
  const toast = useToast();
  const canEditVendor = useHasPermission("vendors.edit");
  const canViewCredentials = useHasPermission("vendors.view_credentials");

  const [revealed, setRevealed] = useState<string | null>(null);
  const [revealing, setRevealing] = useState(false);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const hidePassword = useCallback(() => {
    if (hideTimer.current) {
      clearTimeout(hideTimer.current);
      hideTimer.current = null;
    }
    setRevealed(null);
  }, []);

  useFocusEffect(useCallback(() => () => hidePassword(), [hidePassword]));
  useEffect(
    () => () => {
      if (hideTimer.current) clearTimeout(hideTimer.current);
    },
    []
  );

  const togglePassword = async () => {
    if (revealed !== null) {
      hidePassword();
      return;
    }
    if (!id || revealing) return;
    setRevealing(true);
    try {
      const creds = await fetchVendorCredentials(id);
      setRevealed(creds.password || "");
      if (hideTimer.current) clearTimeout(hideTimer.current);
      hideTimer.current = setTimeout(() => {
        hideTimer.current = null;
        setRevealed(null);
      }, PASSWORD_VISIBLE_MS);
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Could not load the password.", "error");
    } finally {
      setRevealing(false);
    }
  };

  const [vendor, setVendor] = useState<VendorFull | null>(null);
  const [orders, setOrders] = useState<VendorOrderRow[]>([]);
  const [summary, setSummary] = useState<VendorOrdersSummary | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const [v, o] = await Promise.all([
        fetchVendorFull(id),
        fetchVendorOrders(id, 1).catch(() => null),
      ]);
      setVendor(v);
      setOrders(o?.items || []);
      setSummary(o?.summary || null);
      setHasMore(!!o?.pagination?.has_next);
      setPage(1);
      setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load vendor.");
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

  const loadMore = async () => {
    if (!id || loadingMore || !hasMore) return;
    setLoadingMore(true);
    try {
      const o = await fetchVendorOrders(id, page + 1);
      setOrders((prev) => [...prev, ...o.items]);
      setHasMore(!!o.pagination?.has_next);
      setPage(page + 1);
    } catch {
      // молча — кнопка останется
    } finally {
      setLoadingMore(false);
    }
  };

  if (loading) {
    return (
      <View style={[styles.center, { backgroundColor: theme.bg }]}>
        <Stack.Screen options={{ title: "Vendor" }} />
        <ActivityIndicator color={theme.primary} size="large" />
      </View>
    );
  }

  if (error || !vendor) {
    return (
      <View style={[styles.center, { backgroundColor: theme.bg }]}>
        <Stack.Screen options={{ title: "Vendor" }} />
        <Text style={{ color: theme.danger, textAlign: "center", padding: 24 }}>
          {error || "Vendor not found."}
        </Text>
      </View>
    );
  }

  const mainContact = (vendor.contacts || []).find((c) => c.is_main) || (vendor.contacts || [])[0];
  // Сервер отдаёт готовую ссылку; у старого сервера — считаем сами.
  const siteHref = vendor.website_href ?? websiteHref(vendor.website);
  const siteLabel = vendor.website_label || websiteLabel(vendor.website);
  const openWebsite = siteHref
    ? () => {
        Linking.openURL(siteHref).catch(() => toast.show("Can't open this website.", "error"));
      }
    : undefined;
  const hasCredentials = !!(vendor.portal_login || vendor.has_portal_password);

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
          title: vendor.name || "Vendor",
          headerRight: () =>
            canEditVendor ? (
              <Pressable
                onPress={() => router.push({ pathname: "/vendor-form", params: { id: vendor._id || id } })}
                hitSlop={8}
                style={{ paddingHorizontal: 8 }}
              >
                <Ionicons name="create-outline" size={22} color={theme.primary} />
              </Pressable>
            ) : null,
        }}
      />

      <RowCard>
        <View style={styles.headerRow}>
          <Text style={[styles.title, { color: theme.text }]} numberOfLines={2}>
            {vendor.name || "—"}
          </Text>
          {vendor.is_active === false ? <Badge label="Inactive" tone="muted" /> : null}
        </View>
        {mainContact ? (
          <KV
            label="Contact"
            value={[mainContact.first_name, mainContact.last_name].filter(Boolean).join(" ")}
          />
        ) : null}
        <KV label="Phone" value={mainContact?.phone || ""} />
        <KV label="Email" value={mainContact?.email || ""} />
        <KV label="Website" value={siteLabel} onPress={openWebsite} />
        <KV label="Address" value={vendor.address || ""} />
        {vendor.notes ? <KV label="Notes" value={vendor.notes} /> : null}
      </RowCard>

      {canViewCredentials ? (
        <>
          <Text style={[styles.sectionTitle, { color: theme.muted }]}>PORTAL LOGIN</Text>
          <RowCard>
            {hasCredentials ? (
              <>
                <KV label="Website" value={siteLabel} onPress={openWebsite} />
                <KV label="Login" value={vendor.portal_login || ""} selectable />
                {vendor.has_portal_password ? (
                  <View style={styles.secretRow}>
                    <Text style={[styles.secretLabel, { color: theme.muted }]}>Password</Text>
                    <View style={styles.secretValueWrap}>
                      <Text
                        selectable={revealed !== null}
                        style={[styles.secretValue, { color: theme.text }]}
                        numberOfLines={1}
                      >
                        {revealed ?? PASSWORD_MASK}
                      </Text>
                      <Pressable
                        onPress={togglePassword}
                        disabled={revealing}
                        hitSlop={8}
                        accessibilityRole="button"
                        accessibilityLabel={revealed !== null ? "Hide password" : "Show password"}
                      >
                        {revealing ? (
                          <ActivityIndicator size="small" color={theme.primary} />
                        ) : (
                          <Text style={[styles.secretAction, { color: theme.primary }]}>
                            {revealed !== null ? "Hide" : "Show"}
                          </Text>
                        )}
                      </Pressable>
                    </View>
                  </View>
                ) : null}
                {revealed !== null || vendor.portal_login ? (
                  <Text style={[styles.hint, { color: theme.muted }]}>
                    {revealed !== null
                      ? "Long-press to copy. Hides again in 30 seconds."
                      : "Long-press the login to copy it."}
                  </Text>
                ) : null}
              </>
            ) : (
              <Text style={{ color: theme.muted, fontSize: 13 }}>
                No portal login saved.{canEditVendor ? " Tap the edit button to add one." : ""}
              </Text>
            )}
          </RowCard>
        </>
      ) : null}

      {summary ? (
        <>
          <Text style={[styles.sectionTitle, { color: theme.muted }]}>ORDERS SUMMARY</Text>
          <RowCard>
            <KV label="Total orders" value={String(summary.total_orders)} />
            <KV label="Total amount" value={money(summary.total_amount)} />
            <KV label="Paid" value={money(summary.total_paid)} />
            <KV label="Unpaid" value={money(summary.unpaid)} />
            <KV
              label="Received"
              value={`${summary.received} of ${summary.received + summary.not_received}`}
            />
          </RowCard>
        </>
      ) : null}

      <Text style={[styles.sectionTitle, { color: theme.muted }]}>PART ORDERS</Text>
      {orders.length === 0 ? (
        <RowCard>
          <Text style={{ color: theme.muted, fontSize: 13 }}>No part orders for this vendor.</Text>
        </RowCard>
      ) : (
        orders.map((o) => (
          <RowCard key={o.id}>
            <View style={styles.headerRow}>
              <Text style={{ color: theme.text, fontSize: 14, fontWeight: "700" }}>
                Order #{o.order_number || "—"}
              </Text>
              {o.status === "received" ? (
                <Badge label="Received" tone="success" />
              ) : (
                <Badge label={o.status || "ordered"} tone="warning" />
              )}
            </View>
            <View style={styles.headerRow}>
              <Text style={{ color: theme.muted, fontSize: 13 }}>
                {o.items_count} item{o.items_count === 1 ? "" : "s"} · {o.created_at || "—"}
              </Text>
              <Text style={{ color: theme.text, fontSize: 14, fontWeight: "700" }}>
                {money(o.total_amount)}
              </Text>
            </View>
          </RowCard>
        ))
      )}

      {hasMore ? (
        <Pressable
          style={[styles.moreBtn, { borderColor: theme.border, backgroundColor: theme.surface }]}
          onPress={loadMore}
          disabled={loadingMore}
        >
          {loadingMore ? (
            <ActivityIndicator color={theme.primary} size="small" />
          ) : (
            <Text style={{ color: theme.text, fontWeight: "600" }}>Load more</Text>
          )}
        </Pressable>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 12, paddingBottom: 40 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 },
  title: { fontSize: 17, fontWeight: "800", flexShrink: 1 },
  sectionTitle: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: 14, marginBottom: 2 },
  secretRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 12 },
  secretLabel: { fontSize: 13 },
  secretValueWrap: { flexDirection: "row", alignItems: "center", gap: 12, flexShrink: 1 },
  secretValue: {
    fontSize: 13,
    fontWeight: "500",
    flexShrink: 1,
    fontFamily: Platform.select({ ios: "Menlo", default: "monospace" }),
  },
  secretAction: { fontSize: 13, fontWeight: "700" },
  hint: { fontSize: 12, marginTop: 2 },
  moreBtn: {
    borderWidth: 1,
    borderRadius: 12,
    paddingVertical: 12,
    alignItems: "center",
    marginTop: 10,
  },
});
