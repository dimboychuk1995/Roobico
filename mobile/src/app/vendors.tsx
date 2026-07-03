import Ionicons from "@expo/vector-icons/Ionicons";
import { Stack, useRouter } from "expo-router";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { ListScreen } from "@/components/list-screen";
import { Badge, KV, RowCard } from "@/components/ui";
import { VendorRow, fetchVendors } from "@/lib/api";
import { useTheme } from "@/lib/theme";

function VendorCard({ item }: { item: VendorRow }) {
  const theme = useTheme();
  return (
    <RowCard>
      <View style={styles.topRow}>
        <Text style={[styles.title, { color: theme.text }]} numberOfLines={1}>
          {item.name || "—"}
        </Text>
        {!item.is_active ? <Badge label="Inactive" tone="muted" /> : null}
      </View>
      <KV label="Contact" value={item.contact_name} />
      <KV label="Phone" value={item.phone} />
      <KV label="Email" value={item.email} />
      <KV label="Website" value={item.website} />
    </RowCard>
  );
}

export default function VendorsScreen() {
  const router = useRouter();
  return (
    <>
    <Stack.Screen
      options={{
        headerRight: () => (
          <Pressable onPress={() => router.push("/vendor-form")} hitSlop={8} style={{ paddingHorizontal: 8 }}>
            <Ionicons name="add" size={26} color="#16a34a" />
          </Pressable>
        ),
      }}
    />
    <ListScreen<VendorRow>
      fetchPage={fetchVendors}
      renderItem={(item) => (
        <Pressable onPress={() => router.push({ pathname: "/vendor/[id]", params: { id: item.id } })}>
          <VendorCard item={item} />
        </Pressable>
      )}
      keyExtractor={(item) => item.id}
      searchPlaceholder="Search vendors..."
      emptyTitle="No vendors found"
      emptyHint="Try a different search."
    />
    </>
  );
}

const styles = StyleSheet.create({
  topRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 },
  title: { fontSize: 15, fontWeight: "700", flexShrink: 1 },
});
