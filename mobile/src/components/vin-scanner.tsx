import Ionicons from "@expo/vector-icons/Ionicons";
import { BarcodeScanningResult, CameraView, useCameraPermissions } from "expo-camera";
import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { useToast } from "@/context/toast";
import { ApiError, normalizeVin, scanVinImage } from "@/lib/api";
import { useTheme } from "@/lib/theme";

/**
 * Сканер VIN: живой баркод-скан камерой (Code 39/128, DataMatrix, QR,
 * PDF417) + кнопка «прочитать текст» — фото таблички уходит на сервер
 * в OCR. Наружу отдаёт только валидный 17-символьный VIN.
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
  const toast = useToast();
  const [permission, requestPermission] = useCameraPermissions();
  const [busy, setBusy] = useState(false);
  const [torch, setTorch] = useState(false);
  const scannedRef = useRef(false);
  const camRef = useRef<CameraView>(null);

  useEffect(() => {
    if (visible) {
      scannedRef.current = false;
      setBusy(false);
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
    if (scannedRef.current || busy) return;
    const vin = normalizeVin(res.data || "");
    if (!vin) return; // не VIN (чужой штрихкод в кадре) — продолжаем сканировать
    scannedRef.current = true;
    onVin(vin);
  };

  const takeTextPhoto = async () => {
    if (!camRef.current || busy || scannedRef.current) return;
    setBusy(true);
    try {
      const photo = await camRef.current.takePictureAsync({ quality: 0.7 });
      if (!photo?.uri) throw new Error("no photo");
      const res = await scanVinImage({ uri: photo.uri, name: "vin.jpg", type: "image/jpeg" });
      if (scannedRef.current) return;
      scannedRef.current = true;
      onVin(res.vin);
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Couldn't read the VIN — try again.", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: "#000" }}>
        {permission?.granted ? (
          <CameraView
            ref={camRef}
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
          <Text style={styles.hint}>
            Point at the VIN barcode — or line up the VIN text{"\n"}and tap "Read VIN text"
          </Text>
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

        {/* Нижняя кнопка: OCR по фото */}
        {permission?.granted ? (
          <View style={styles.bottomBar}>
            <Pressable
              style={[styles.photoBtn, { backgroundColor: theme.primary, opacity: busy ? 0.7 : 1 }]}
              onPress={takeTextPhoto}
              disabled={busy}
            >
              {busy ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <>
                  <Ionicons name="text-outline" size={20} color="#fff" />
                  <Text style={{ color: "#fff", fontWeight: "800", fontSize: 15 }}>Read VIN text</Text>
                </>
              )}
            </Pressable>
          </View>
        ) : null}
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
  bottomBar: { position: "absolute", bottom: 44, left: 0, right: 0, alignItems: "center" },
  photoBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    borderRadius: 999,
    paddingHorizontal: 24,
    paddingVertical: 14,
  },
  permBox: { flex: 1, alignItems: "center", justifyContent: "center", gap: 14, padding: 32 },
  permText: { color: "#fff", fontSize: 14, textAlign: "center" },
  permBtn: { borderRadius: 10, paddingHorizontal: 20, paddingVertical: 12 },
});
