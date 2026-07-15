import Ionicons from "@expo/vector-icons/Ionicons";
import { Tabs, useRouter } from "expo-router";
import { Pressable } from "react-native";

import { useHasPermission } from "@/context/auth";
import { useTheme } from "@/lib/theme";

export default function TabsLayout() {
  const theme = useTheme();
  const router = useRouter();
  // Табы гейтятся по правам: у роли mechanic нет dashboard/customers/parts —
  // сервер и так вернёт 403, но пустые экраны показывать незачем.
  const canDashboard = useHasPermission("dashboard.view");
  const canCustomers = useHasPermission("customers.view");
  const canParts = useHasPermission("parts.view");

  const addButton = (onPress: () => void) => () => (
    <Pressable onPress={onPress} hitSlop={8} style={{ paddingHorizontal: 14 }}>
      <Ionicons name="add" size={26} color={theme.primary} />
    </Pressable>
  );

  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: theme.primary,
        tabBarInactiveTintColor: theme.muted,
        tabBarStyle: { backgroundColor: theme.tabBar, borderTopColor: theme.border },
        headerStyle: { backgroundColor: theme.surface },
        headerTintColor: theme.text,
        sceneStyle: { backgroundColor: theme.bg },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Dashboard",
          href: canDashboard ? undefined : null,
          tabBarIcon: ({ color, size }) => <Ionicons name="grid-outline" color={color} size={size} />,
        }}
      />
      <Tabs.Screen
        name="work-orders"
        options={{
          title: "Work Orders",
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="document-text-outline" color={color} size={size} />
          ),
          headerRight: addButton(() => router.push("/work-order-form")),
        }}
      />
      <Tabs.Screen
        name="customers"
        options={{
          title: "Customers",
          href: canCustomers ? undefined : null,
          tabBarIcon: ({ color, size }) => <Ionicons name="people-outline" color={color} size={size} />,
          headerRight: addButton(() => router.push("/customer-form")),
        }}
      />
      <Tabs.Screen
        name="parts"
        options={{
          title: "Parts",
          href: canParts ? undefined : null,
          tabBarIcon: ({ color, size }) => <Ionicons name="construct-outline" color={color} size={size} />,
        }}
      />
      <Tabs.Screen
        name="more"
        options={{
          title: "More",
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="ellipsis-horizontal-outline" color={color} size={size} />
          ),
        }}
      />
    </Tabs>
  );
}
