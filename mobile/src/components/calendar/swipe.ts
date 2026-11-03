/**
 * Горизонтальный свайп «вперёд/назад» поверх вертикального ScrollView:
 * перехватываем жест в capture-фазе только когда движение явно
 * горизонтальное и палец один, вертикальная прокрутка остаётся за
 * ScrollView, двухпальцевый щипок (масштаб) — за GestureDetector.
 */
import { useMemo, useRef } from "react";
import { PanResponder } from "react-native";

export function useSwipe(onSwipe: (dir: 1 | -1) => void) {
  const cb = useRef(onSwipe);
  cb.current = onSwipe;
  // Если в ходе жеста появился второй палец — это щипок, а не свайп.
  const multiTouch = useRef(false);
  return useMemo(
    () =>
      PanResponder.create({
        onMoveShouldSetPanResponderCapture: (_, g) =>
          g.numberActiveTouches === 1 && Math.abs(g.dx) > 14 && Math.abs(g.dx) > Math.abs(g.dy) * 1.6,
        onPanResponderGrant: () => {
          multiTouch.current = false;
        },
        onPanResponderMove: (_, g) => {
          if (g.numberActiveTouches > 1) multiTouch.current = true;
        },
        onPanResponderTerminationRequest: () => false,
        onPanResponderRelease: (_, g) => {
          if (!multiTouch.current && Math.abs(g.dx) > 50 && Math.abs(g.dx) > Math.abs(g.dy)) {
            cb.current(g.dx < 0 ? 1 : -1);
          }
        },
      }),
    []
  ).panHandlers;
}
