/**
 * Push-уведомления (Expo): регистрация токена устройства на бэкенде и
 * снятие при логауте.
 *
 * Токен получаем только на физическом устройстве; в Expo Go (SDK 53+)
 * remote push недоступен — там регистрация тихо пропускается, приложение
 * работает как раньше. Полноценные пуши — в TestFlight/dev-сборке.
 */
import Constants from "expo-constants";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";

import { apiRegisterPushToken } from "@/lib/api";

// Как показывать уведомление, пришедшее при ОТКРЫТОМ приложении.
// На вебе expo-notifications не реализован — не трогаем вовсе.
if (Platform.OS !== "web") {
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowBanner: true,
      shouldShowList: true,
      shouldPlaySound: true,
      shouldSetBadge: false,
    }),
  });
}

let registeredToken: string | null = null;

/** Токен, зарегистрированный в этой сессии приложения (для logout). */
export function getRegisteredPushToken(): string | null {
  return registeredToken;
}

/**
 * Запросить разрешение, получить Expo-токен и отправить его на сервер.
 * Все сбои — не критичны (нет разрешения, Expo Go, нет сети): просто
 * остаёмся без пушей.
 */
export async function registerPushToken(): Promise<void> {
  try {
    if (Platform.OS === "web") return; // на вебе пушей нет
    if (!Device.isDevice) return; // симулятор — пушей не бывает

    if (Platform.OS === "android") {
      await Notifications.setNotificationChannelAsync("default", {
        name: "Default",
        importance: Notifications.AndroidImportance.MAX,
        sound: "default",
      });
    }

    const perms = await Notifications.getPermissionsAsync();
    let status = perms.status;
    if (status !== "granted") {
      const req = await Notifications.requestPermissionsAsync();
      status = req.status;
    }
    if (status !== "granted") return;

    const projectId: string | undefined =
      Constants.expoConfig?.extra?.eas?.projectId;
    const tokenResponse = await Notifications.getExpoPushTokenAsync(
      projectId ? { projectId } : undefined
    );
    const token = tokenResponse.data;
    if (!token) return;

    await apiRegisterPushToken(token, Platform.OS);
    registeredToken = token;
  } catch (e) {
    // Expo Go без поддержки remote push / сеть моргнула — работаем без пушей.
    console.warn("Push token registration skipped:", e);
  }
}

export function clearRegisteredPushToken(): void {
  registeredToken = null;
}
