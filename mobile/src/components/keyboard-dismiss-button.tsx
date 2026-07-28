/**
 * Плавающая кнопка «спрятать клавиатуру»: появляется над клавиатурой,
 * когда та открыта. Подключена глобально в корневом layout'е.
 */
import Ionicons from "@expo/vector-icons/Ionicons";
import { useEffect, useState } from "react";
import { Keyboard, Platform, Pressable, StyleSheet } from "react-native";

import { useTheme } from "@/lib/theme";

export function KeyboardDismissButton() {
  const theme = useTheme();
  const [kbHeight, setKbHeight] = useState(0);

  useEffect(() => {
    // На iOS Will-события дают высоту до появления; на Android их нет.
    const showEvent = Platform.OS === "ios" ? "keyboardWillShow" : "keyboardDidShow";
    const hideEvent = Platform.OS === "ios" ? "keyboardWillHide" : "keyboardDidHide";
    const showSub = Keyboard.addListener(showEvent, (e) => {
      setKbHeight(e.endCoordinates?.height || 0);
    });
    const hideSub = Keyboard.addListener(hideEvent, () => setKbHeight(0));
    return () => {
      showSub.remove();
      hideSub.remove();
    };
  }, []);

  if (!kbHeight) return null;

  return (
    <Pressable
      style={[
        styles.btn,
        {
          bottom: kbHeight + 10,
          backgroundColor: theme.surface,
          borderColor: theme.border,
          shadowColor: "#000",
        },
      ]}
      onPress={() => Keyboard.dismiss()}
      hitSlop={8}
      accessibilityLabel="Hide keyboard"
    >
      <Ionicons name="chevron-down" size={20} color={theme.primary} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  btn: {
    position: "absolute",
    right: 12,
    width: 40,
    height: 40,
    borderRadius: 20,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
    elevation: 4,
    shadowOpacity: 0.15,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    zIndex: 1000,
  },
});
