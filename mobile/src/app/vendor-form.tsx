import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { useEffect, useState } from "react";
import { ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView, View } from "react-native";

import { Field, SubmitButton, SwitchRow } from "@/components/form";
import { useHasPermission } from "@/context/auth";
import { useToast } from "@/context/toast";
import {
  ApiError,
  ContactPayload,
  VendorCredentialsPayload,
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
  // Учётка портала: только с vendors.view_credentials. Новый вендор создаётся
  // через /api/mobile/vendors, а логин/пароль следом сохраняет веб-апдейт
  // (нужен vendors.edit) — без него поля при создании не показываем.
  const canViewCredentials = useHasPermission("vendors.view_credentials");
  const canEditVendor = useHasPermission("vendors.edit");
  const showCredentials = canViewCredentials && (isEdit || canEditVendor);

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
  const [portalLogin, setPortalLogin] = useState("");
  const [portalPassword, setPortalPassword] = useState("");
  const [hasSavedPassword, setHasSavedPassword] = useState(false);
  const [clearPassword, setClearPassword] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  useEffect(() => {
    if (!isEdit || !id) return;
    fetchVendorFull(id)
      .then((v) => {
        setName(v.name || "");
        setWebsite(v.website || "");
        setAddress(v.address || "");
        setNotes(v.notes || "");
        setIsActive(v.is_active !== false);
        setPortalLogin(v.portal_login || "");
        setHasSavedPassword(!!v.has_portal_password);
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
    // Пустой пароль — сохранённый остаётся; Remove — удалить.
    const credentials: VendorCredentialsPayload = showCredentials
      ? {
          portal_login: portalLogin.trim(),
          ...(portalPassword.trim() && !clearPassword ? { portal_password: portalPassword } : {}),
          ...(clearPassword ? { portal_password_clear: true } : {}),
        }
      : {};

    setBusy(true);
    try {
      if (isEdit && id) {
        await updateVendor(id, { ...payload, ...credentials });
        toast.show("Vendor updated.", "success");
      } else {
        const created = await createVendor(payload);
        if (showCredentials && (credentials.portal_login || credentials.portal_password)) {
          try {
            await updateVendor(created.id, { ...payload, ...credentials });
          } catch (e) {
            // Вендор уже создан — повторное создание дало бы дубль; учётку
            // можно добавить через Edit.
            const reason = e instanceof ApiError ? e.message : "Save failed.";
            toast.show(`Vendor created, but the portal login was not saved: ${reason}`, "error");
            router.back();
            return;
          }
        }
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
        <Field
          label="Website"
          value={website}
          onChangeText={setWebsite}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          placeholder="fleetpride.com (no https:// needed)"
        />
        {showCredentials ? (
          <>
            <Field
              label="Portal login"
              value={portalLogin}
              onChangeText={setPortalLogin}
              autoCapitalize="none"
              autoCorrect={false}
              autoComplete="off"
              textContentType="none"
              importantForAutofill="no"
              placeholder="Login on the vendor's website"
              maxLength={256}
            />
            <Field
              label="Portal password"
              value={portalPassword}
              onChangeText={setPortalPassword}
              secureTextEntry={!showPassword}
              autoCapitalize="none"
              autoCorrect={false}
              autoComplete="off"
              textContentType="none"
              importantForAutofill="no"
              editable={!clearPassword}
              placeholder={hasSavedPassword ? "Saved — leave blank to keep it" : "Optional"}
              maxLength={256}
            />
            <SwitchRow label="Show password" value={showPassword} onValueChange={setShowPassword} />
            {hasSavedPassword ? (
              <SwitchRow
                label="Remove saved password"
                value={clearPassword}
                onValueChange={(v) => {
                  setClearPassword(v);
                  if (v) setPortalPassword("");
                }}
              />
            ) : null}
          </>
        ) : null}
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
