import Ionicons from "@expo/vector-icons/Ionicons";
import { BarcodeScanningResult, CameraView, useCameraPermissions } from "expo-camera";
import { useEffect, useRef, useState } from "react";
import { Modal, Pressable, StyleSheet, Text, View } from "react-native";

import { normalizeVin } from "@/lib/api";
import { useTheme } from "@/lib/theme";

/**
 * Сканер VIN-баркода: механик просто наводит камеру на штрихкод
 * (Code 39/128, DataMatrix, QR, PDF417) — VIN подхватывается сам,
 * без кнопок. Чужие штрихкоды в кадре игнорируются, пока не увидим
 * валидный 17-символьный VIN.
 */
export function VinScannerModal({
  visible,
  onClose,
  onVin,
}: {
  visible: boolean;
  onClose: () => void;
  onVin: (vin: string) => void;
}) {
  const theme = useTheme();
  const [permission, requestPermission] = useCameraPermissions();
  const [torch, setTorch] = useState(false);
  const scannedRef = useRef(false);

  useEffect(() => {
    if (visible) {
      scannedRef.current = false;
      setTorch(false);
    }
  }, [visible]);

  useEffect(() => {
    if (visible && permission && !permission.granted && permission.canAskAgain) {
      requestPermission();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, permission?.granted]);

  const handleBarcode = (res: BarcodeScanningResult) => {
    if (scannedRef.current) return;
    const vin = normalizeVin(res.data || "");
    if (!vin) return; // не VIN — продолжаем сканировать
    scannedRef.current = true;
    onVin(vin);
  };

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: "#000" }}>
        {permission?.granted ? (
          <CameraView
            style={StyleSheet.absoluteFill}
            facing="back"
            enableTorch={torch}
            barcodeScannerSettings={{
              barcodeTypes: ["code39", "code128", "datamatrix", "qr", "pdf417", "itf14"],
            }}
            onBarcodeScanned={handleBarcode}
          />
        ) : (
          <View style={styles.permBox}>
            <Ionicons name="camera-outline" size={40} color="#fff" />
            <Text style={styles.permText}>
              Camera access is needed to scan VIN barcodes.
            </Text>
            {permission && !permission.granted && permission.canAskAgain ? (
              <Pressable style={[styles.permBtn, { backgroundColor: theme.primary }]} onPress={requestPermission}>
                <Text style={{ color: "#fff", fontWeight: "700" }}>Allow camera</Text>
              </Pressable>
            ) : (
              <Text style={[styles.permText, { fontSize: 12 }]}>
                Enable camera access for Roobico in system settings.
              </Text>
            )}
          </View>
        )}

        {/* Рамка прицела + подсказка */}
        <View pointerEvents="none" style={styles.overlay}>
          <View style={styles.frame} />
          <Text style={styles.hint}>Point the camera at the VIN barcode</Text>
        </View>

        {/* Верхняя панель: закрыть + фонарик */}
        <View style={styles.topBar}>
          <Pressable onPress={onClose} hitSlop={12} style={styles.roundBtn}>
            <Ionicons name="close" size={26} color="#fff" />
          </Pressable>
          <Pressable onPress={() => setTorch((t) => !t)} hitSlop={12} style={styles.roundBtn}>
            <Ionicons name={torch ? "flashlight" : "flashlight-outline"} size={24} color="#fff" />
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: { ...StyleSheet.absoluteFillObject, alignItems: "center", justifyContent: "center" },
  frame: {
    width: "82%",
    height: 120,
    borderWidth: 2,
    borderColor: "rgba(255,255,255,0.85)",
    borderRadius: 14,
  },
  hint: {
    color: "#fff",
    fontSize: 13,
    textAlign: "center",
    marginTop: 14,
    textShadowColor: "rgba(0,0,0,0.8)",
    textShadowRadius: 4,
  },
  topBar: {
    position: "absolute",
    top: 54,
    left: 16,
    right: 16,
    flexDirection: "row",
    justifyContent: "space-between",
  },
  roundBtn: {
    backgroundColor: "rgba(0,0,0,0.45)",
    borderRadius: 22,
    width: 44,
    height: 44,
    alignItems: "center",
    justifyContent: "center",
  },
  permBox: { flex: 1, alignItems: "center", justifyContent: "center", gap: 14, padding: 32 },
  permText: { color: "#fff", fontSize: 14, textAlign: "center" },
  permBtn: { borderRadius: 10, paddingHorizontal: 20, paddingVertical: 12 },
});
