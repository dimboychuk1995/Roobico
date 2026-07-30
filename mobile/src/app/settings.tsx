import Ionicons from "@expo/vector-icons/Ionicons";
import { useEffect, useState } from "react";
import { Alert, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { useAuth } from "@/context/auth";
import { ApiError, getBaseUrl } from "@/lib/api";
import {
  ThemePreference,
  loadThemePreference,
  setThemePreference,
  useTheme,
} from "@/lib/theme";

const THEME_OPTIONS: { key: ThemePreference; label: string; icon: string }[] = [
  { key: "system", label: "System", icon: "phone-portrait-outline" },
  { key: "light", label: "Light", icon: "sunny-outline" },
  { key: "dark", label: "Dark", icon: "moon-outline" },
];

export default function SettingsScreen() {
  const theme = useTheme();
  const { session, logout, setActiveShop } = useAuth();
  const [switching, setSwitching] = useState("");
  const [themePref, setThemePref] = useState<ThemePreference>("system");

  useEffect(() => {
    loadThemePreference().then(setThemePref);
  }, []);

  if (!session) return null;

  const onSelectTheme = (pref: ThemePreference) => {
    setThemePref(pref);
    setThemePreference(pref);
  };

  const onSelectShop = async (shopId: string) => {
    if (shopId === session.active_shop_id || switching) return;
    setSwitching(shopId);
    try {
      await setActiveShop(shopId);
    } catch (e) {
      Alert.alert("Error", e instanceof ApiError ? e.message : "Failed to switch shop.");
    } finally {
      setSwitching("");
    }
  };

  const onLogout = () => {
    Alert.alert("Log out", "Sign out of Roobico?", [
      { text: "Cancel", style: "cancel" },
      { text: "Log out", style: "destructive", onPress: () => logout() },
    ]);
  };

  return (
    <ScrollView style={{ backgroundColor: theme.bg }} contentContainerStyle={styles.container}>
      <Text style={[styles.sectionTitle, { color: theme.muted }]}>APPEARANCE</Text>
      <View style={styles.themeRow}>
        {THEME_OPTIONS.map((opt) => {
          const active = themePref === opt.key;
          return (
            <Pressable
              key={opt.key}
              onPress={() => onSelectTheme(opt.key)}
              style={[
                styles.themeChip,
                {
                  backgroundColor: active ? theme.primary : theme.surface,
                  borderColor: active ? theme.primary : theme.border,
                },
              ]}
            >
              <Ionicons
                name={opt.icon as any}
                size={16}
                color={active ? "#fff" : theme.muted}
              />
              <Text style={{ color: active ? "#fff" : theme.text, fontWeight: "600", fontSize: 13 }}>
                {opt.label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      <Text style={[styles.sectionTitle, { color: theme.muted }]}>ACTIVE SHOP</Text>
      <View style={[styles.card, { backgroundColor: theme.surface, borderColor: theme.border }]}>
        {session.shops.map((shop, idx) => {
          const active = shop.id === session.active_shop_id;
          return (
            <Pressable
              key={shop.id}
              onPress={() => onSelectShop(shop.id)}
              style={[
                styles.shopRow,
                idx > 0 && { borderTopWidth: 1, borderTopColor: theme.border },
              ]}
            >
              <Text style={{ color: theme.text, fontSize: 15, flex: 1 }}>{shop.name}</Text>
              {active ? <Ionicons name="checkmark-circle" size={20} color={theme.primary} /> : null}
            </Pressable>
          );
        })}
      </View>

      <Text style={[styles.sectionTitle, { color: theme.muted }]}>ACCOUNT</Text>
      <View style={[styles.card, { backgroundColor: theme.surface, borderColor: theme.border }]}>
        <View style={styles.kvRow}>
          <Text style={{ color: theme.muted, fontSize: 13 }}>Email</Text>
          <Text style={{ color: theme.text, fontSize: 13, fontWeight: "500" }}>
            {session.user.email}
          </Text>
        </View>
        <View style={styles.kvRow}>
          <Text style={{ color: theme.muted, fontSize: 13 }}>Organization</Text>
          <Text style={{ color: theme.text, fontSize: 13, fontWeight: "500" }}>
            {session.tenant.name}
          </Text>
        </View>
        <View style={styles.kvRow}>
          <Text style={{ color: theme.muted, fontSize: 13 }}>Role</Text>
          <Text style={{ color: theme.text, fontSize: 13, fontWeight: "500" }}>
            {session.user.role || "—"}
          </Text>
        </View>
        <View style={styles.kvRow}>
          <Text style={{ color: theme.muted, fontSize: 13 }}>Server</Text>
          <Text style={{ color: theme.text, fontSize: 13, fontWeight: "500" }}>{getBaseUrl()}</Text>
        </View>
      </View>

      <Pressable
        onPress={onLogout}
        style={[styles.logout, { backgroundColor: theme.surface, borderColor: theme.border }]}
      >
        <Ionicons name="log-out-outline" size={18} color={theme.danger} />
        <Text style={{ color: theme.danger, fontSize: 15, fontWeight: "600" }}>Log out</Text>
      </Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 12, gap: 8, paddingBottom: 32 },
  sectionTitle: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: 8 },
  themeRow: { flexDirection: "row", gap: 8 },
  themeChip: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    borderWidth: 1,
    borderRadius: 10,
    paddingVertical: 11,
  },
  card: { borderWidth: 1, borderRadius: 12, paddingHorizontal: 14 },
  shopRow: { flexDirection: "row", alignItems: "center", paddingVertical: 13, gap: 8 },
  kvRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 10,
    gap: 12,
  },
  logout: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    borderWidth: 1,
    borderRadius: 12,
    paddingVertical: 14,
    marginTop: 8,
  },
});
