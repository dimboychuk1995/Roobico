import Ionicons from "@expo/vector-icons/Ionicons";
import { Href, useRouter } from "expo-router";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { useAuth } from "@/context/auth";
import { useTheme } from "@/lib/theme";

const ITEMS: { title: string; subtitle: string; icon: string; href: Href }[] = [
  { title: "Search", subtitle: "Find anything across the shop", icon: "search-outline", href: "/search" },
  { title: "Vendors", subtitle: "Suppliers and balances", icon: "business-outline", href: "/vendors" },
  { title: "Calendar", subtitle: "Appointments", icon: "calendar-outline", href: "/calendar" },
  { title: "Reports", subtitle: "Sales, payments, parts", icon: "bar-chart-outline", href: "/reports" },
  { title: "Settings", subtitle: "Shop, account", icon: "settings-outline", href: "/settings" },
];

export default function MoreScreen() {
  const theme = useTheme();
  const router = useRouter();
  const { session } = useAuth();

  return (
    <ScrollView style={{ backgroundColor: theme.bg }} contentContainerStyle={styles.container}>
      {ITEMS.map((item) => (
        <Pressable
          key={item.title}
          onPress={() => router.push(item.href)}
          style={({ pressed }) => [
            styles.item,
            {
              backgroundColor: pressed ? theme.surfaceSoft : theme.surface,
              borderColor: theme.border,
            },
          ]}
        >
          <View style={[styles.iconWrap, { backgroundColor: theme.surfaceSoft }]}>
            <Ionicons name={item.icon as any} size={22} color={theme.primary} />
          </View>
          <View style={styles.itemText}>
            <Text style={[styles.itemTitle, { color: theme.text }]}>{item.title}</Text>
            <Text style={[styles.itemSubtitle, { color: theme.muted }]}>{item.subtitle}</Text>
          </View>
          <Ionicons name="chevron-forward" size={18} color={theme.muted} />
        </Pressable>
      ))}

      {session ? (
        <Text style={[styles.footer, { color: theme.muted }]}>
          {session.user.email} · {session.tenant.name}
        </Text>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 12, gap: 8 },
  item: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    borderWidth: 1,
    borderRadius: 12,
    padding: 14,
  },
  iconWrap: {
    width: 40,
    height: 40,
    borderRadius: 10,
    alignItems: "center",
    justifyContent: "center",
  },
  itemText: { flex: 1 },
  itemTitle: { fontSize: 15, fontWeight: "600" },
  itemSubtitle: { fontSize: 12, marginTop: 1 },
  footer: { textAlign: "center", marginTop: 16, fontSize: 12 },
});
