/**
 * Таймер labor-строки WO (механик-режим): Start job / Stop job + затраченное
 * время. Над одной работой могут работать несколько механиков одновременно —
 * elapsed = завершённые сессии + все идущие таймеры от их server-таймстемпов
 * (drift-free), тикер живёт только пока есть идущие.
 */
import Ionicons from "@expo/vector-icons/Ionicons";
import { useEffect, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";

import { useToast } from "@/context/toast";
import {
  ApiError,
  MechRunningUser,
  TimerResponse,
  startLaborTimer,
  stopLaborTimer,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

function fmtDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

export function LaborTimer({
  woId,
  laborId,
  completedSeconds,
  runningUsers,
  myRunning,
  serverOffsetMs,
  onChanged,
}: {
  woId: string;
  laborId: string;
  /** Секунды завершённых сессий (без идущих). */
  completedSeconds: number;
  /** Все идущие таймеры на этой строке (мои и чужие). */
  runningUsers: MechRunningUser[];
  myRunning: boolean;
  /** server_now - Date.now() на момент последнего ответа. */
  serverOffsetMs: number;
  onChanged: (res: TimerResponse) => void;
}) {
  const theme = useTheme();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [, setTick] = useState(0);

  const isRunning = runningUsers.length > 0;

  useEffect(() => {
    if (!isRunning) return;
    const handle = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(handle);
  }, [isRunning]);

  const nowMs = Date.now() + serverOffsetMs;
  const total = runningUsers.reduce((sum, u) => {
    const started = Date.parse(u.started_at);
    return Number.isFinite(started) ? sum + Math.max(0, (nowMs - started) / 1000) : sum;
  }, completedSeconds);

  const others = runningUsers.filter((u) => !u.mine);
  const othersLabel = others.map((u) => u.user_name || "other").join(", ");

  const onPress = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const res = myRunning ? await stopLaborTimer() : await startLaborTimer(woId, laborId);
      if (!myRunning && res.stopped_previous && res.stopped_previous.work_order_id !== woId) {
        toast.show(`Timer on WO #${res.stopped_previous.wo_number ?? ""} stopped.`, "info");
      }
      onChanged(res);
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Timer error.", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={[styles.row, { borderTopColor: theme.border }]}>
      <View style={styles.info}>
        <Text style={[styles.tracked, { color: theme.muted }]}>
          Tracked: <Text style={{ fontWeight: "700", color: theme.text }}>{fmtDuration(total)}</Text>
        </Text>
        {othersLabel ? (
          <Text style={[styles.other, { color: theme.danger }]}>● {othersLabel} working</Text>
        ) : null}
      </View>
      <Pressable
        onPress={onPress}
        disabled={busy}
        style={[
          styles.btn,
          { backgroundColor: myRunning ? theme.danger : theme.primary, opacity: busy ? 0.6 : 1 },
        ]}
      >
        {busy ? (
          <ActivityIndicator color="#fff" size="small" />
        ) : (
          <>
            <Ionicons name={myRunning ? "stop" : "play"} size={16} color="#fff" />
            <Text style={styles.btnText}>{myRunning ? "Stop job" : "Start job"}</Text>
          </>
        )}
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10,
    borderTopWidth: 1,
    marginTop: 8,
    paddingTop: 10,
  },
  info: { flex: 1, minWidth: 0, gap: 2 },
  tracked: { fontSize: 13 },
  other: { fontSize: 12, fontWeight: "700" },
  btn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    borderRadius: 10,
    paddingHorizontal: 16,
    paddingVertical: 10,
    minWidth: 120,
  },
  btnText: { color: "#fff", fontSize: 14, fontWeight: "700" },
});
