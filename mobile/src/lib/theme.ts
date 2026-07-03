/**
 * Палитра Roobico — согласована с веб-версией (public.css / theme_dark.css).
 */
export const palette = {
  light: {
    bg: "#f1f5f2",
    surface: "#ffffff",
    surfaceSoft: "#f7faf7",
    border: "#d4ddd6",
    text: "#1a2e22",
    muted: "#5a7262",
    primary: "#16a34a",
    primaryDeep: "#145e33",
    danger: "#c43b3b",
    warning: "#c48a1a",
    tabBar: "#ffffff",
  },
  dark: {
    bg: "#212121",
    surface: "#2a2a2a",
    surfaceSoft: "#232323",
    border: "#3a3a3a",
    text: "#ececec",
    muted: "#9b9b9b",
    primary: "#22c55e",
    primaryDeep: "#16a34a",
    danger: "#ef4444",
    warning: "#f59e0b",
    tabBar: "#171717",
  },
};

export type Palette = typeof palette.light;

import { useColorScheme } from "react-native";

export function useTheme(): Palette {
  const scheme = useColorScheme();
  return scheme === "dark" ? palette.dark : palette.light;
}
