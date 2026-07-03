/**
 * Лёгкие тосты (успех/ошибка) поверх экрана — аналог appAlert в вебе.
 */
import React, { createContext, useCallback, useContext, useRef, useState } from "react";
import { Animated, StyleSheet, Text } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

type ToastKind = "success" | "error" | "info";

interface ToastState {
  show: (message: string, kind?: ToastKind) => void;
}

const ToastContext = createContext<ToastState | null>(null);

const COLORS: Record<ToastKind, { bg: string; fg: string }> = {
  success: { bg: "#16a34a", fg: "#ffffff" },
  error: { bg: "#dc2626", fg: "#ffffff" },
  info: { bg: "#334155", fg: "#ffffff" },
};

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const insets = useSafeAreaInsets();
  const [message, setMessage] = useState("");
  const [kind, setKind] = useState<ToastKind>("info");
  const opacity = useRef(new Animated.Value(0)).current;
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const show = useCallback(
    (msg: string, k: ToastKind = "info") => {
      setMessage(msg);
      setKind(k);
      if (hideTimer.current) clearTimeout(hideTimer.current);
      Animated.timing(opacity, { toValue: 1, duration: 180, useNativeDriver: true }).start();
      hideTimer.current = setTimeout(() => {
        Animated.timing(opacity, { toValue: 0, duration: 250, useNativeDriver: true }).start();
      }, 2600);
    },
    [opacity]
  );

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      <Animated.View
        pointerEvents="none"
        style={[
          styles.toast,
          { top: insets.top + 8, backgroundColor: COLORS[kind].bg, opacity },
        ]}
      >
        <Text style={[styles.text, { color: COLORS[kind].fg }]} numberOfLines={3}>
          {message}
        </Text>
      </Animated.View>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastState {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside ToastProvider");
  return ctx;
}

const styles = StyleSheet.create({
  toast: {
    position: "absolute",
    left: 16,
    right: 16,
    borderRadius: 12,
    paddingHorizontal: 16,
    paddingVertical: 12,
    zIndex: 1000,
    elevation: 8,
    shadowColor: "#000",
    shadowOpacity: 0.25,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 4 },
  },
  text: { fontSize: 14, fontWeight: "600", textAlign: "center" },
});
