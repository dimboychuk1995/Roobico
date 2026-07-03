import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { useEffect, useState } from "react";
import { ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView, View } from "react-native";

import { Field, SubmitButton, SwitchRow } from "@/components/form";
import { useToast } from "@/context/toast";
import {
  ApiError,
  ContactPayload,
  createCustomer,
  fetchCustomerDetails,
  updateCustomer,
} from "@/lib/api";
import { useTheme } from "@/lib/theme";

export default function CustomerFormScreen() {
  const { id } = useLocalSearchParams<{ id?: string }>();
  const isEdit = !!id;
  const theme = useTheme();
  const toast = useToast();
  const router = useRouter();

  const [loading, setLoading] = useState(isEdit);
  const [busy, setBusy] = useState(false);

  const [companyName, setCompanyName] = useState("");
  const [address, setAddress] = useState("");
  const [taxable, setTaxable] = useState(true);
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [otherContacts, setOtherContacts] = useState<ContactPayload[]>([]);

  useEffect(() => {
    if (!isEdit || !id) return;
    fetchCustomerDetails(id)
      .then((c) => {
        setCompanyName(c.company_name);
        setAddress(c.address);
        setTaxable(c.taxable);
        const main = c.contacts.find((x) => x.is_main) || c.contacts[0];
        if (main) {
          setFirstName(main.first_name || "");
          setLastName(main.last_name || "");
          setPhone(main.phone || "");
          setEmail(main.email || "");
        }
        // Остальные контакты сохраняем как есть — форма правит только главный.
        setOtherContacts(
          c.contacts
            .filter((x) => x !== main)
            .map((x) => ({
              first_name: x.first_name,
              last_name: x.last_name,
              phone: x.phone,
              email: x.email,
              is_main: false,
            }))
        );
      })
      .catch((e) => {
        toast.show(e instanceof ApiError ? e.message : "Failed to load customer.", "error");
      })
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const onSubmit = async () => {
    const hasMainContact = !!(firstName.trim() || lastName.trim());
    if (!companyName.trim() && !hasMainContact) {
      toast.show("Company name or contact name is required.", "error");
      return;
    }
    if (address.trim().length < 5) {
      toast.show("Customer address is required.", "error");
      return;
    }

    const contacts: ContactPayload[] = [];
    if (hasMainContact || phone.trim() || email.trim()) {
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
      company_name: companyName.trim(),
      address: address.trim(),
      taxable,
      contacts,
    };

    setBusy(true);
    try {
      if (isEdit && id) {
        await updateCustomer(id, payload);
        toast.show("Customer updated.", "success");
      } else {
        await createCustomer(payload);
        toast.show("Customer created.", "success");
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
        <Stack.Screen options={{ title: "Customer" }} />
        <ActivityIndicator color={theme.primary} size="large" />
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: theme.bg }}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <Stack.Screen options={{ title: isEdit ? "Edit Customer" : "New Customer" }} />
      <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 48 }} keyboardShouldPersistTaps="handled">
        <Field label="Company name" value={companyName} onChangeText={setCompanyName} placeholder="Company LLC" />
        <Field label="Address" value={address} onChangeText={setAddress} placeholder="Street, city, state" />
        <SwitchRow label="Taxable" value={taxable} onValueChange={setTaxable} />

        <Field label="Contact first name" value={firstName} onChangeText={setFirstName} />
        <Field label="Contact last name" value={lastName} onChangeText={setLastName} />
        <Field label="Phone" value={phone} onChangeText={setPhone} keyboardType="phone-pad" />
        <Field label="Email" value={email} onChangeText={setEmail} keyboardType="email-address" autoCapitalize="none" />

        <SubmitButton title={isEdit ? "Save changes" : "Create customer"} onPress={onSubmit} busy={busy} />
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
