import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { useEffect, useState } from "react";
import { ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView, View } from "react-native";

import { Field, SubmitButton } from "@/components/form";
import { useToast } from "@/context/toast";
import {
  ApiError,
  ContactPayload,
  createVendor,
  fetchVendorFull,
  updateVendor,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

export default function VendorFormScreen() {
  const { id } = useLocalSearchParams<{ id?: string }>();
  const isEdit = !!id;
  const theme = useTheme();
  const toast = useToast();
  const router = useRouter();

  const [loading, setLoading] = useState(isEdit);
  const [busy, setBusy] = useState(false);

  const [name, setName] = useState("");
  const [website, setWebsite] = useState("");
  const [address, setAddress] = useState("");
  const [notes, setNotes] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [otherContacts, setOtherContacts] = useState<ContactPayload[]>([]);
  const [isActive, setIsActive] = useState(true);

  useEffect(() => {
    if (!isEdit || !id) return;
    fetchVendorFull(id)
      .then((v) => {
        setName(v.name || "");
        setWebsite(v.website || "");
        setAddress(v.address || "");
        setNotes(v.notes || "");
        setIsActive(v.is_active !== false);
        const contacts = v.contacts || [];
        const main = contacts.find((x) => x.is_main) || contacts[0];
        if (main) {
          setFirstName(main.first_name || "");
          setLastName(main.last_name || "");
          setPhone(main.phone || "");
          setEmail(main.email || "");
        }
        setOtherContacts(
          contacts
            .filter((x) => x !== main)
            .map((x) => ({ ...x, is_main: false }))
        );
      })
      .catch((e) => {
        toast.show(e instanceof ApiError ? e.message : "Failed to load vendor.", "error");
      })
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const onSubmit = async () => {
    if (!name.trim()) {
      toast.show("Vendor name is required.", "error");
      return;
    }
    const contacts: ContactPayload[] = [];
    if (firstName.trim() || lastName.trim() || phone.trim() || email.trim()) {
      contacts.push({
        first_name: firstName.trim(),
        last_name: lastName.trim(),
        phone: phone.trim(),
        email: email.trim(),
        is_main: true,
      });
    }
    contacts.push(...otherContacts);

    const payload = {
      name: name.trim(),
      website: website.trim(),
      address: address.trim(),
      notes: notes.trim(),
      contacts,
      is_active: isActive,
    };

    setBusy(true);
    try {
      if (isEdit && id) {
        await updateVendor(id, payload);
        toast.show("Vendor updated.", "success");
      } else {
        await createVendor(payload);
        toast.show("Vendor created.", "success");
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
        <Stack.Screen options={{ title: "Vendor" }} />
        <ActivityIndicator color={theme.primary} size="large" />
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: theme.bg }}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <Stack.Screen options={{ title: isEdit ? "Edit Vendor" : "New Vendor" }} />
      <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 48 }} keyboardShouldPersistTaps="handled">
        <Field label="Name" value={name} onChangeText={setName} placeholder="Vendor name" />
        <Field label="Website" value={website} onChangeText={setWebsite} autoCapitalize="none" keyboardType="url" />
        <Field label="Address" value={address} onChangeText={setAddress} />
        <Field label="Notes" value={notes} onChangeText={setNotes} multiline />

        <Field label="Contact first name" value={firstName} onChangeText={setFirstName} />
        <Field label="Contact last name" value={lastName} onChangeText={setLastName} />
        <Field label="Phone" value={phone} onChangeText={setPhone} keyboardType="phone-pad" />
        <Field label="Email" value={email} onChangeText={setEmail} keyboardType="email-address" autoCapitalize="none" />

        <SubmitButton title={isEdit ? "Save changes" : "Create vendor"} onPress={onSubmit} busy={busy} />
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
