/**
 * Горизонтальный свайп «вперёд/назад» поверх вертикального ScrollView:
 * перехватываем жест в capture-фазе только когда движение явно
 * горизонтальное, вертикальная прокрутка остаётся за ScrollView.
 */
import { useMemo, useRef } from "react";
import { PanResponder } from "react-native";

export function useSwipe(onSwipe: (dir: 1 | -1) => void) {
  const cb = useRef(onSwipe);
  cb.current = onSwipe;
  return useMemo(
    () =>
      PanResponder.create({
        onMoveShouldSetPanResponderCapture: (_, g) =>
          Math.abs(g.dx) > 14 && Math.abs(g.dx) > Math.abs(g.dy) * 1.6,
        onPanResponderTerminationRequest: () => false,
        onPanResponderRelease: (_, g) => {
          if (Math.abs(g.dx) > 50 && Math.abs(g.dx) > Math.abs(g.dy)) {
            cb.current(g.dx < 0 ? 1 : -1);
          }
        },
      }),
    []
  ).panHandlers;
}
