import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import Ionicons from "@expo/vector-icons/Ionicons";

import { Field, SubmitButton } from "@/components/form";
import { VinScannerModal } from "@/components/vin-scanner";
import { useToast } from "@/context/toast";
import {
  ApiError,
  createUnit,
  decodeVin,
  fetchUnitDetails,
  updateUnit,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

export default function UnitFormScreen() {
  const { id, customerId } = useLocalSearchParams<{ id?: string; customerId?: string }>();
  const isEdit = !!id;
  const theme = useTheme();
  const toast = useToast();
  const router = useRouter();

  const [loading, setLoading] = useState(isEdit);
  const [busy, setBusy] = useState(false);
  const [vinBusy, setVinBusy] = useState(false);
  const [scannerOpen, setScannerOpen] = useState(false);

  const [unitNumber, setUnitNumber] = useState("");
  const [vin, setVin] = useState("");
  const [year, setYear] = useState("");
  const [make, setMake] = useState("");
  const [model, setModel] = useState("");
  const [type, setType] = useState("");
  const [mileage, setMileage] = useState("");

  useEffect(() => {
    if (!isEdit || !id) return;
    fetchUnitDetails(id)
      .then((u) => {
        setUnitNumber(u.unit_number);
        setVin(u.vin);
        setYear(u.year);
        setMake(u.make);
        setModel(u.model);
        setType(u.type);
        setMileage(u.mileage != null ? String(u.mileage) : "");
      })
      .catch((e) => {
        toast.show(e instanceof ApiError ? e.message : "Failed to load unit.", "error");
      })
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const onDecodeVin = async (vinOverride?: string) => {
    const v = (vinOverride || vin).trim().toUpperCase();
    if (v.length !== 17) {
      toast.show("VIN must be exactly 17 characters.", "error");
      return;
    }
    setVinBusy(true);
    try {
      const data = await decodeVin(v);
      if (data.year) setYear(data.year);
      if (data.make) setMake(data.make);
      if (data.model) setModel(data.model);
      if (data.type) setType(data.type);
      if (data.warning) {
        toast.show(
          data.suggested_vin ? `NHTSA suggested VIN: ${data.suggested_vin}` : data.warning,
          "info"
        );
      } else {
        toast.show("VIN decoded.", "success");
      }
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "VIN lookup failed.", "error");
    } finally {
      setVinBusy(false);
    }
  };

  const onSubmit = async () => {
    if (!unitNumber.trim() && !vin.trim()) {
      toast.show("Unit number or VIN is required.", "error");
      return;
    }
    const payload = {
      customer_id: customerId,
      unit_number: unitNumber.trim(),
      vin: vin.trim().toUpperCase(),
      year: year.trim(),
      make: make.trim(),
      model: model.trim(),
      type: type.trim(),
      mileage: mileage.trim(),
    };
    setBusy(true);
    try {
      if (isEdit && id) {
        await updateUnit(id, payload);
        toast.show("Unit updated.", "success");
      } else {
        await createUnit(payload);
        toast.show("Unit created.", "success");
      }
      router.back();
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Save failed.", "error");
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: theme.bg }}>
        <Stack.Screen options={{ title: "Unit" }} />
        <ActivityIndicator color={theme.primary} size="large" />
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: theme.bg }}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <Stack.Screen options={{ title: isEdit ? "Edit Unit" : "New Unit" }} />
      <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 48 }} keyboardShouldPersistTaps="handled">
        <Field label="Unit number" value={unitNumber} onChangeText={setUnitNumber} placeholder="T-101" />

        <View style={styles.vinRow}>
          <View style={{ flex: 1 }}>
            <Field
              label="VIN"
              value={vin}
              onChangeText={(v) => setVin(v.toUpperCase())}
              autoCapitalize="characters"
              autoCorrect={false}
              maxLength={17}
              placeholder="17 characters"
            />
          </View>
          <Pressable
            style={[styles.vinBtn, { backgroundColor: theme.primary, opacity: vinBusy ? 0.7 : 1 }]}
            onPress={() => setScannerOpen(true)}
            disabled={vinBusy}
          >
            <Ionicons name="barcode-outline" size={18} color="#fff" />
          </Pressable>
          <Pressable
            style={[styles.vinBtn, { backgroundColor: theme.primary, opacity: vinBusy ? 0.7 : 1 }]}
            onPress={() => onDecodeVin()}
            disabled={vinBusy}
          >
            {vinBusy ? (
              <ActivityIndicator color="#fff" size="small" />
            ) : (
              <Text style={{ color: "#fff", fontWeight: "700", fontSize: 13 }}>Decode</Text>
            )}
          </Pressable>
        </View>

        <Field label="Year" value={year} onChangeText={setYear} keyboardType="number-pad" />
        <Field label="Make" value={make} onChangeText={setMake} />
        <Field label="Model" value={model} onChangeText={setModel} />
        <Field label="Type" value={type} onChangeText={setType} />
        <Field label="Mileage" value={mileage} onChangeText={setMileage} keyboardType="number-pad" />

        <SubmitButton title={isEdit ? "Save changes" : "Create unit"} onPress={onSubmit} busy={busy} />
      </ScrollView>

      {/* Скан VIN: баркод или фото таблички → поле VIN + автодекод NHTSA. */}
      <VinScannerModal
        visible={scannerOpen}
        onClose={() => setScannerOpen(false)}
        onVin={(v) => {
          setScannerOpen(false);
          setVin(v);
          onDecodeVin(v);
        }}
      />
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  vinRow: { flexDirection: "row", alignItems: "flex-end", gap: 8 },
  vinBtn: {
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    marginBottom: 1,
  },
});
