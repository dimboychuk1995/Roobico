import { useRouter } from "expo-router";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { ListScreen } from "@/components/list-screen";
import { Badge, KV, RowCard } from "@/components/ui";
import { CustomerRow, fetchCustomers } from "@/lib/api";
import { useTheme } from "@/lib/theme";

function CustomerCard({ item }: { item: CustomerRow }) {
  const theme = useTheme();
  const title = item.company_name || item.contact_name || "—";
  return (
    <RowCard>
      <View style={styles.topRow}>
        <Text style={[styles.title, { color: theme.text }]} numberOfLines={1}>
          {title}
        </Text>
        {!item.is_active ? <Badge label="Inactive" tone="muted" /> : null}
      </View>
      {item.company_name && item.contact_name ? <KV label="Contact" value={item.contact_name} /> : null}
      <KV label="Phone" value={item.phone} />
      <KV label="Email" value={item.email} />
      <KV label="Address" value={item.address} />
    </RowCard>
  );
}

export default function CustomersScreen() {
  const router = useRouter();
  return (
    <ListScreen<CustomerRow>
      fetchPage={fetchCustomers}
      renderItem={(item) => (
        <Pressable onPress={() => router.push({ pathname: "/customer/[id]", params: { id: item.id } })}>
          <CustomerCard item={item} />
        </Pressable>
      )}
      keyExtractor={(item) => item.id}
      searchPlaceholder="Search customers..."
      emptyTitle="No customers found"
      emptyHint="Try a different search."
    />
  );
}

const styles = StyleSheet.create({
  topRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 },
  title: { fontSize: 15, fontWeight: "700", flexShrink: 1 },
});
