import Ionicons from "@expo/vector-icons/Ionicons";
import { Stack, useRouter } from "expo-router";
import { useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import * as ImagePicker from "expo-image-picker";

import { SubmitButton } from "@/components/form";
import { RowCard } from "@/components/ui";
import { useToast } from "@/context/toast";
import {
  ApiError,
  PartSearchItem,
  VendorRow,
  createPartsOrder,
  fetchVendors,
  money,
  parseInvoiceScan,
  searchParts,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";
import { SearchPickerModal } from "@/components/search-picker";

interface OrderItem {
  part_id: string;
  part_number: string;
  description: string;
  quantity: number;
  price: number;
}

export default function PartsOrderFormScreen() {
  const theme = useTheme();
  const toast = useToast();
  const router = useRouter();

  const [vendor, setVendor] = useState<{ id: string; name: string } | null>(null);
  const [items, setItems] = useState<OrderItem[]>([]);
  const [vendorModal, setVendorModal] = useState(false);
  const [partModal, setPartModal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [scanning, setScanning] = useState(false);

  // AI-скан инвойса: фото → распознанный вендор и позиции.
  const scanInvoice = async (fromCamera: boolean) => {
    const res = fromCamera
      ? await (async () => {
          const perm = await ImagePicker.requestCameraPermissionsAsync();
          if (!perm.granted) {
            toast.show("Camera permission denied.", "error");
            return null;
          }
          return ImagePicker.launchCameraAsync({ quality: 0.8 });
        })()
      : await ImagePicker.launchImageLibraryAsync({ quality: 0.8 });
    if (!res || res.canceled || !res.assets?.[0]) return;
    const asset = res.assets[0];
    setScanning(true);
    try {
      const parsed = await parseInvoiceScan({
        uri: asset.uri,
        name: asset.fileName || "invoice.jpg",
        type: asset.mimeType || "image/jpeg",
      });
      if (parsed.vendor_match) {
        setVendor({ id: parsed.vendor_match.vendor_id, name: parsed.vendor_match.vendor_name });
      } else if (parsed.vendor_name) {
        toast.show(`Vendor "${parsed.vendor_name}" not found — select manually.`, "info");
      }
      let matched = 0;
      let skipped = 0;
      const newItems: OrderItem[] = [];
      for (const it of parsed.items || []) {
        const qty = Number(it.quantity ?? it.qty ?? 1) || 1;
        const price = Number(it.price ?? it.unit_price ?? 0) || 0;
        if (it.matched_part) {
          matched += 1;
          newItems.push({
            part_id: it.matched_part.part_id,
            part_number: it.matched_part.part_number,
            description: it.matched_part.description,
            quantity: qty,
            price: price || it.matched_part.average_cost || 0,
          });
        } else {
          skipped += 1;
        }
      }
      if (newItems.length) setItems((prev) => [...prev, ...newItems]);
      toast.show(
        `AI: ${matched} item(s) matched${skipped ? `, ${skipped} not in catalog — add manually` : ""}.`,
        matched ? "success" : "info"
      );
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Invoice scan failed.", "error");
    } finally {
      setScanning(false);
    }
  };

  const addPart = (p: PartSearchItem) => {
    setPartModal(false);
    setItems((prev) => {
      const existing = prev.find((x) => x.part_id === p.id);
      if (existing) {
        return prev.map((x) => (x.part_id === p.id ? { ...x, quantity: x.quantity + 1 } : x));
      }
      return [
        ...prev,
        {
          part_id: p.id,
          part_number: p.part_number,
          description: p.description,
          quantity: 1,
          price: p.average_cost || 0,
        },
      ];
    });
  };

  const total = items.reduce((s, i) => s + i.quantity * i.price, 0);

  const onSubmit = async () => {
    if (!vendor) {
      toast.show("Select a vendor.", "error");
      return;
    }
    const valid = items.filter((i) => i.quantity > 0);
    if (!valid.length) {
      toast.show("Add at least one part.", "error");
      return;
    }
    setBusy(true);
    try {
      await createPartsOrder({
        vendor_id: vendor.id,
        items: valid.map((i) => ({ part_id: i.part_id, quantity: i.quantity, price: i.price })),
      });
      toast.show("Order created.", "success");
      router.back();
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Failed to create order.", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: theme.bg }}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <Stack.Screen options={{ title: "New Parts Order" }} />
      <ScrollView contentContainerStyle={{ padding: 12, paddingBottom: 48 }} keyboardShouldPersistTaps="handled">
        <Text style={[styles.sectionTitle, { color: theme.muted }]}>VENDOR</Text>
        <Pressable
          style={[styles.picker, { backgroundColor: theme.surface, borderColor: theme.border }]}
          onPress={() => setVendorModal(true)}
        >
          <Text style={{ color: vendor ? theme.text : theme.muted, fontSize: 15 }}>
            {vendor?.name || "Select vendor…"}
          </Text>
          <Ionicons name="chevron-down" size={16} color={theme.muted} />
        </Pressable>

        <View style={styles.scanRow}>
          <Text style={[styles.sectionTitle, { color: theme.muted, marginTop: 0, marginBottom: 0 }]}>ITEMS</Text>
          <View style={{ flexDirection: "row", gap: 14, alignItems: "center" }}>
            {scanning ? (
              <Text style={{ color: theme.primary, fontSize: 12, fontWeight: "700" }}>AI scanning…</Text>
            ) : (
              <>
                <Pressable onPress={() => scanInvoice(true)} hitSlop={8}>
                  <Ionicons name="camera-outline" size={20} color={theme.primary} />
                </Pressable>
                <Pressable onPress={() => scanInvoice(false)} hitSlop={8}>
                  <Ionicons name="sparkles-outline" size={20} color={theme.primary} />
                </Pressable>
              </>
            )}
          </View>
        </View>
        {items.map((it, idx) => (
          <RowCard key={it.part_id}>
            <View style={styles.itemRow}>
              <View style={{ flex: 1 }}>
                <Text style={{ color: theme.text, fontSize: 14, fontWeight: "600" }} numberOfLines={1}>
                  {it.part_number}
                </Text>
                <Text style={{ color: theme.muted, fontSize: 12 }} numberOfLines={1}>
                  {it.description}
                </Text>
              </View>
              <TextInput
                style={[styles.qtyInput, { borderColor: theme.border, color: theme.text }]}
                value={String(it.quantity || "")}
                onChangeText={(v) =>
                  setItems((prev) =>
                    prev.map((x, i) => (i === idx ? { ...x, quantity: parseInt(v, 10) || 0 } : x))
                  )
                }
                keyboardType="number-pad"
              />
              <TextInput
                style={[styles.priceInput, { borderColor: theme.border, color: theme.text }]}
                value={String(it.price || "")}
                onChangeText={(v) => {
                  const num = parseFloat(v.replace(",", "."));
                  setItems((prev) =>
                    prev.map((x, i) => (i === idx ? { ...x, price: Number.isFinite(num) ? num : 0 } : x))
                  );
                }}
                keyboardType="decimal-pad"
              />
              <Pressable onPress={() => setItems((prev) => prev.filter((_, i) => i !== idx))} hitSlop={8}>
                <Ionicons name="close-circle" size={20} color={theme.danger} />
              </Pressable>
            </View>
          </RowCard>
        ))}

        <Pressable
          style={[styles.addBtn, { borderColor: theme.border, backgroundColor: theme.surface }]}
          onPress={() => setPartModal(true)}
        >
          <Ionicons name="add" size={18} color={theme.primary} />
          <Text style={{ color: theme.primary, fontWeight: "700" }}>Add part</Text>
        </Pressable>

        <RowCard>
          <View style={styles.totalRow}>
            <Text style={{ color: theme.text, fontWeight: "800", fontSize: 15 }}>Total</Text>
            <Text style={{ color: theme.text, fontWeight: "800", fontSize: 15 }}>{money(total)}</Text>
          </View>
        </RowCard>

        <SubmitButton title="Create order" onPress={onSubmit} busy={busy} />
      </ScrollView>

      <SearchPickerModal<VendorRow>
        visible={vendorModal}
        onClose={() => setVendorModal(false)}
        title="Select vendor"
        placeholder="Search vendors…"
        search={(q) => fetchVendors(q, 1).then((d) => d.items.filter((v) => v.is_active))}
        renderLabel={(v) => v.name}
        onPick={(v) => {
          setVendor({ id: v.id, name: v.name });
          setVendorModal(false);
        }}
      />
      <SearchPickerModal<PartSearchItem>
        visible={partModal}
        onClose={() => setPartModal(false)}
        title="Add part"
        placeholder="Part number or description…"
        search={(q) => (q.length >= 2 ? searchParts(q) : Promise.resolve([]))}
        renderLabel={(p) => `${p.part_number} — ${p.description || ""} (×${p.in_stock})`}
        onPick={addPart}
      />
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  sectionTitle: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: 14, marginBottom: 4 },
  picker: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 13,
  },
  itemRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  qtyInput: { borderWidth: 1, borderRadius: 8, width: 52, textAlign: "center", paddingVertical: 6, fontSize: 14 },
  priceInput: { borderWidth: 1, borderRadius: 8, width: 74, textAlign: "center", paddingVertical: 6, fontSize: 14 },
  addBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    borderWidth: 1,
    borderRadius: 12,
    paddingVertical: 12,
    marginTop: 8,
  },
  totalRow: { flexDirection: "row", justifyContent: "space-between" },
  scanRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 14,
    marginBottom: 4,
  },
});
