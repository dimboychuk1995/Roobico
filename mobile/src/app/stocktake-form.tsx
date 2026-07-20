/**
 * Создание инвентаризации: имя (опционально) + охват — локация (с
 * подлокациями) и/или категория. После создания открывается экран подсчёта.
 */
import Ionicons from "@expo/vector-icons/Ionicons";
import { Stack, useRouter } from "expo-router";
import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { useToast } from "@/context/toast";
import {
  ApiError,
  StocktakeLocationOption,
  createStocktake,
  fetchStocktakeOptions,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

interface PickerOption {
  id: string;
  label: string;
}

function OptionPicker({
  visible,
  title,
  options,
  onSelect,
  onClose,
}: {
  visible: boolean;
  title: string;
  options: PickerOption[];
  onSelect: (opt: PickerOption | null) => void;
  onClose: () => void;
}) {
  const theme = useTheme();
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.modalBackdrop}>
        <View style={[styles.modalCard, { backgroundColor: theme.surface, borderColor: theme.border }]}>
          <Text style={[styles.modalTitle, { color: theme.text }]}>{title}</Text>
          <FlatList
            data={[{ id: "", label: "— Any —" }, ...options]}
            keyExtractor={(o) => o.id || "any"}
            style={{ maxHeight: 380 }}
            renderItem={({ item }) => (
              <Pressable
                style={[styles.optionRow, { borderColor: theme.border }]}
                onPress={() => {
                  onSelect(item.id ? item : null);
                  onClose();
                }}
              >
                <Text style={{ color: theme.text, fontSize: 15 }}>{item.label}</Text>
              </Pressable>
            )}
          />
          <Pressable style={[styles.cancelBtn, { borderColor: theme.border }]} onPress={onClose}>
            <Text style={{ color: theme.text, fontWeight: "600" }}>Cancel</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

export default function StocktakeFormScreen() {
  const theme = useTheme();
  const toast = useToast();
  const router = useRouter();

  const [name, setName] = useState("");
  const [locations, setLocations] = useState<StocktakeLocationOption[]>([]);
  const [categories, setCategories] = useState<{ id: string; name: string }[]>([]);
  const [location, setLocation] = useState<PickerOption | null>(null);
  const [category, setCategory] = useState<PickerOption | null>(null);
  const [pickerOpen, setPickerOpen] = useState<"location" | "category" | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetchStocktakeOptions()
      .then((d) => {
        setLocations(d.locations || []);
        setCategories(d.categories || []);
      })
      .catch(() => {
        // без справочников форма всё равно позволяет счёт по всему складу
      })
      .finally(() => setLoading(false));
  }, []);

  const onSubmit = async () => {
    setBusy(true);
    try {
      const res = await createStocktake({
        name: name.trim(),
        location_id: location?.id || "",
        category_id: category?.id || "",
      });
      toast.show(`Stocktake ST-${res.number} started.`, "success");
      router.replace({ pathname: "/stocktake/[id]", params: { id: res.id } });
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Failed to start stocktake.", "error");
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <View style={[styles.center, { backgroundColor: theme.bg }]}>
        <Stack.Screen options={{ title: "New Stocktake" }} />
        <ActivityIndicator color={theme.primary} size="large" />
      </View>
    );
  }

  return (
    <ScrollView style={{ backgroundColor: theme.bg }} contentContainerStyle={styles.container}>
      <Stack.Screen options={{ title: "New Stocktake" }} />

      <Text style={{ color: theme.muted, fontSize: 13 }}>
        The warehouse keeps working while you count: each entered quantity is compared against
        system stock at the moment of counting, and adjustments are applied as deltas.
      </Text>

      <Text style={[styles.inputLabel, { color: theme.muted }]}>NAME (OPTIONAL)</Text>
      <TextInput
        style={[styles.input, { backgroundColor: theme.surface, borderColor: theme.border, color: theme.text }]}
        value={name}
        onChangeText={setName}
        placeholder="e.g. July cycle count"
        placeholderTextColor={theme.muted}
        maxLength={120}
      />

      <Text style={[styles.inputLabel, { color: theme.muted }]}>LOCATION SCOPE</Text>
      <Pressable
        style={[styles.selectRow, { backgroundColor: theme.surface, borderColor: theme.border }]}
        onPress={() => setPickerOpen("location")}
      >
        <Text style={{ color: location ? theme.text : theme.muted, fontSize: 15, flex: 1 }}>
          {location ? location.label : "Whole warehouse"}
        </Text>
        <Ionicons name="chevron-down" size={16} color={theme.muted} />
      </Pressable>
      <Text style={{ color: theme.muted, fontSize: 12 }}>
        Sub-locations of the selected location are included.
      </Text>

      <Text style={[styles.inputLabel, { color: theme.muted }]}>CATEGORY SCOPE</Text>
      <Pressable
        style={[styles.selectRow, { backgroundColor: theme.surface, borderColor: theme.border }]}
        onPress={() => setPickerOpen("category")}
      >
        <Text style={{ color: category ? theme.text : theme.muted, fontSize: 15, flex: 1 }}>
          {category ? category.label : "All categories"}
        </Text>
        <Ionicons name="chevron-down" size={16} color={theme.muted} />
      </Pressable>

      <Pressable
        style={[styles.primaryBtn, { backgroundColor: theme.primary, opacity: busy ? 0.7 : 1 }]}
        onPress={onSubmit}
        disabled={busy}
      >
        {busy ? (
          <ActivityIndicator color="#fff" size="small" />
        ) : (
          <>
            <Ionicons name="clipboard-outline" size={18} color="#fff" />
            <Text style={styles.primaryBtnText}>Start counting</Text>
          </>
        )}
      </Pressable>

      <OptionPicker
        visible={pickerOpen === "location"}
        title="Location scope"
        options={locations.map((l) => ({ id: l.id, label: `${"— ".repeat(l.depth)}${l.path.split(" › ").pop() || l.path}` }))}
        onSelect={(opt) =>
          setLocation(
            opt ? { id: opt.id, label: locations.find((l) => l.id === opt.id)?.path || opt.label } : null
          )
        }
        onClose={() => setPickerOpen(null)}
      />
      <OptionPicker
        visible={pickerOpen === "category"}
        title="Category scope"
        options={categories.map((c) => ({ id: c.id, label: c.name }))}
        onSelect={setCategory}
        onClose={() => setPickerOpen(null)}
      />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, paddingBottom: 40, gap: 4 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  inputLabel: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: 16, marginBottom: 4 },
  input: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10, fontSize: 15 },
  selectRow: {
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 12,
  },
  primaryBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    borderRadius: 12,
    paddingVertical: 14,
    marginTop: 24,
  },
  primaryBtnText: { color: "#fff", fontSize: 15, fontWeight: "700" },
  modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "center", padding: 20 },
  modalCard: { borderWidth: 1, borderRadius: 16, padding: 18 },
  modalTitle: { fontSize: 17, fontWeight: "800", marginBottom: 8 },
  optionRow: { borderBottomWidth: StyleSheet.hairlineWidth, paddingVertical: 12 },
  cancelBtn: { borderWidth: 1, borderRadius: 10, paddingVertical: 12, alignItems: "center", marginTop: 12 },
});
