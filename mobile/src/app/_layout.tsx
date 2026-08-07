import { DarkTheme, DefaultTheme, ThemeProvider } from "@react-navigation/native";
import * as Notifications from "expo-notifications";
import { Stack, router } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { useEffect, useRef } from "react";
import { Platform, useColorScheme } from "react-native";

import { KeyboardDismissButton } from "@/components/keyboard-dismiss-button";
import { AuthProvider, useAuth } from "@/context/auth";
import { ToastProvider } from "@/context/toast";
import { loadThemePreference, palette } from "@/lib/theme";

SplashScreen.preventAutoHideAsync();
// Сохранённый выбор темы применяем до первого рендера, насколько возможно.
loadThemePreference();

/**
 * Тап по push-уведомлению → экран WO. Покрывает и запущенное приложение,
 * и холодный старт (useLastNotificationResponse отдаёт «догоняющий» ответ).
 * Если WO из другого магазина — сначала переключаем активный магазин.
 */
function PushNotificationRouter() {
  const { session, setActiveShop } = useAuth();
  const response = Notifications.useLastNotificationResponse();
  const handledId = useRef<string>("");

  useEffect(() => {
    if (!response || !session) return;
    const id = response.notification.request.identifier;
    if (!id || handledId.current === id) return;

    const data = response.notification.request.content.data as Record<string, unknown>;
    const workOrderId = String(data?.work_order_id || "");
    if (!workOrderId) return;
    handledId.current = id;

    (async () => {
      const shopId = String(data?.shop_id || "");
      if (shopId && shopId !== session.active_shop_id) {
        try {
          await setActiveShop(shopId);
        } catch {
          return; // нет доступа к магазину — остаёмся где были
        }
      }
      router.push(`/work-order/${workOrderId}`);
    })();
  }, [response, session, setActiveShop]);

  return null;
}

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
          {/* expo-notifications не реализован на вебе — хук уронит рендер. */}
          {Platform.OS !== "web" ? <PushNotificationRouter /> : null}
          <KeyboardDismissButton />
        </ToastProvider>
      </AuthProvider>
    </ThemeProvider>
  );
}
