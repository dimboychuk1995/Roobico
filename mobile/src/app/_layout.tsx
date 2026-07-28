import { DarkTheme, DefaultTheme, ThemeProvider } from "@react-navigation/native";
import { Stack } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { useEffect } from "react";
import { useColorScheme } from "react-native";

import { KeyboardDismissButton } from "@/components/keyboard-dismiss-button";
import { AuthProvider, useAuth } from "@/context/auth";
import { ToastProvider } from "@/context/toast";
import { palette } from "@/lib/theme";

SplashScreen.preventAutoHideAsync();

function RootNavigator() {
  const { ready, session } = useAuth();
  const scheme = useColorScheme();
  const colors = scheme === "dark" ? palette.dark : palette.light;

  useEffect(() => {
    if (ready) SplashScreen.hideAsync();
  }, [ready]);

  if (!ready) return null;

  const loggedIn = !!session;

  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: colors.surface },
        headerTintColor: colors.text,
        contentStyle: { backgroundColor: colors.bg },
      }}
    >
      <Stack.Protected guard={loggedIn}>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="vendors" options={{ title: "Vendors" }} />
        <Stack.Screen name="calendar" options={{ title: "Calendar" }} />
        <Stack.Screen name="reports" options={{ title: "Reports" }} />
        <Stack.Screen name="settings" options={{ title: "Settings" }} />
        <Stack.Screen name="work-order/[id]" options={{ title: "Work Order" }} />
        <Stack.Screen name="customer/[id]" options={{ title: "Customer" }} />
        <Stack.Screen name="unit/[id]" options={{ title: "Unit" }} />
        <Stack.Screen name="vendor/[id]" options={{ title: "Vendor" }} />
        <Stack.Screen name="part/[id]" options={{ title: "Part" }} />
        <Stack.Screen name="customer-form" options={{ title: "Customer" }} />
        <Stack.Screen name="unit-form" options={{ title: "Unit" }} />
        <Stack.Screen name="vendor-form" options={{ title: "Vendor" }} />
        <Stack.Screen name="work-order-form" options={{ title: "Work Order" }} />
        <Stack.Screen name="parts-order/[id]" options={{ title: "Parts Order" }} />
        <Stack.Screen name="parts-order-form" options={{ title: "Parts Order" }} />
        <Stack.Screen name="stocktake/[id]" options={{ title: "Stocktake" }} />
        <Stack.Screen name="stocktake-form" options={{ title: "New Stocktake" }} />
        <Stack.Screen name="search" options={{ title: "Search" }} />
      </Stack.Protected>
      <Stack.Protected guard={!loggedIn}>
        <Stack.Screen name="login" options={{ headerShown: false }} />
      </Stack.Protected>
    </Stack>
  );
}

export default function RootLayout() {
  const scheme = useColorScheme();
  return (
    <ThemeProvider value={scheme === "dark" ? DarkTheme : DefaultTheme}>
      <AuthProvider>
        <ToastProvider>
          <RootNavigator />
          <KeyboardDismissButton />
        </ToastProvider>
      </AuthProvider>
    </ThemeProvider>
  );
}
