/**
 * Блок вложений: превью изображений/файлов + добавление с камеры или из
 * галереи + удаление. Используется на деталках WO, юнита, клиента и т.д.
 */
import Ionicons from "@expo/vector-icons/Ionicons";
import * as ImagePicker from "expo-image-picker";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Image,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { useToast } from "@/context/toast";
import {
  ApiError,
  AttachmentItem,
  attachmentDownloadUrl,
  deleteAttachment,
  fetchAttachments,
  uploadAttachment,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

export function AttachmentsBlock({
  entityType,
  entityId,
  parentId,
  title,
}: {
  entityType: string;
  entityId: string;
  /** Для вложений на конкретную работу WO: parent_id = labor_id. */
  parentId?: string;
  title?: string;
}) {
  const theme = useTheme();
  const toast = useToast();

  const [items, setItems] = useState<AttachmentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);

  const load = useCallback(async () => {
    if (!entityId) {
      setLoading(false);
      return;
    }
    try {
      setItems(await fetchAttachments(entityType, entityId, parentId));
    } catch {
      // отсутствие права attachments.view — просто не показываем
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [entityType, entityId, parentId]);

  useEffect(() => {
    load();
  }, [load]);

  const upload = async (asset: ImagePicker.ImagePickerAsset) => {
    setUploading(true);
    try {
      const name = asset.fileName || `photo_${Date.now()}.jpg`;
      await uploadAttachment(
        entityType,
        entityId,
        {
          uri: asset.uri,
          name,
          type: asset.mimeType || "image/jpeg",
        },
        parentId
      );
      toast.show("Uploaded.", "success");
      load();
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Upload failed.", "error");
    } finally {
      setUploading(false);
    }
  };

  const fromCamera = async () => {
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    if (!perm.granted) {
      toast.show("Camera permission denied.", "error");
      return;
    }
    const res = await ImagePicker.launchCameraAsync({ quality: 0.7 });
    if (!res.canceled && res.assets?.[0]) upload(res.assets[0]);
  };

  const fromGallery = async () => {
    const res = await ImagePicker.launchImageLibraryAsync({ quality: 0.7 });
    if (!res.canceled && res.assets?.[0]) upload(res.assets[0]);
  };

  const onDelete = (item: AttachmentItem) => {
    Alert.alert("Delete file", `Delete "${item.filename}"?`, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete",
        style: "destructive",
        onPress: async () => {
          try {
            await deleteAttachment(item.id);
            load();
          } catch (e) {
            toast.show(e instanceof ApiError ? e.message : "Delete failed.", "error");
          }
        },
      },
    ]);
  };

  return (
    <View>
      <View style={styles.headerRow}>
        <Text style={[styles.title, { color: theme.muted }]}>
          {(title || "ATTACHMENTS").toUpperCase()} ({items.length})
        </Text>
        <View style={{ flexDirection: "row", gap: 14 }}>
          <Pressable onPress={fromCamera} hitSlop={8} disabled={uploading}>
            <Ionicons name="camera-outline" size={20} color={theme.primary} />
          </Pressable>
          <Pressable onPress={fromGallery} hitSlop={8} disabled={uploading}>
            <Ionicons name="images-outline" size={20} color={theme.primary} />
          </Pressable>
        </View>
      </View>

      {loading ? (
        <ActivityIndicator color={theme.primary} style={{ marginVertical: 12 }} />
      ) : (
        <View style={styles.grid}>
          {items.map((item) => (
            <Pressable
              key={item.id}
              onLongPress={() => onDelete(item)}
              style={[styles.tile, { borderColor: theme.border, backgroundColor: theme.surface }]}
            >
              {item.is_image ? (
                <Image
                  source={{ uri: attachmentDownloadUrl(item.id) }}
                  style={styles.thumb}
                  resizeMode="cover"
                />
              ) : (
                <View style={styles.fileTile}>
                  <Ionicons name="document-outline" size={26} color={theme.muted} />
                </View>
              )}
              <Text style={[styles.fileName, { color: theme.muted }]} numberOfLines={1}>
                {item.filename}
              </Text>
            </Pressable>
          ))}
          {uploading ? (
            <View style={[styles.tile, styles.fileTile, { borderColor: theme.border }]}>
              <ActivityIndicator color={theme.primary} />
            </View>
          ) : null}
          {items.length === 0 && !uploading ? (
            <Text style={{ color: theme.muted, fontSize: 12 }}>
              No files yet. Long-press a file to delete it.
            </Text>
          ) : null}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  headerRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 14,
    marginBottom: 6,
  },
  title: { fontSize: 11, fontWeight: "700", letterSpacing: 1 },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  tile: { width: 84, borderWidth: 1, borderRadius: 10, overflow: "hidden" },
  thumb: { width: "100%", height: 64 },
  fileTile: { width: "100%", height: 64, alignItems: "center", justifyContent: "center" },
  fileName: { fontSize: 10, paddingHorizontal: 4, paddingVertical: 3 },
});
