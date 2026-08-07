import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { useAuth } from "@/context/auth";
import { ApiError, getBaseUrl, loadBaseUrl, setBaseUrl } from "@/lib/api";
import { useTheme } from "@/lib/theme";

export default function LoginScreen() {
  const theme = useTheme();
  const { login } = useAuth();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [server, setServer] = useState("");
  const [showServer, setShowServer] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    loadBaseUrl().then((url) => setServer(url));
  }, []);

  const onSubmit = async () => {
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      if (server.trim()) await setBaseUrl(server.trim());
      await login(email.trim(), password);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Login failed.");
    } finally {
      setBusy(false);
    }
  };

  const inputStyle = [
    styles.input,
    { backgroundColor: theme.surface, borderColor: theme.border, color: theme.text },
  ];

  return (
    <KeyboardAvoidingView
      style={[styles.flex, { backgroundColor: theme.bg }]}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
        <Text style={[styles.brand, { color: theme.primary }]}>ROOBICO</Text>
        <Text style={[styles.subtitle, { color: theme.muted }]}>Shop management on the go</Text>

        <View style={styles.form}>
          <Text style={[styles.label, { color: theme.muted }]}>EMAIL</Text>
          <TextInput
            style={inputStyle}
            value={email}
            onChangeText={setEmail}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="email-address"
            textContentType="emailAddress"
            placeholder="you@company.com"
            placeholderTextColor={theme.muted}
          />

          <Text style={[styles.label, { color: theme.muted }]}>PASSWORD</Text>
          <TextInput
            style={inputStyle}
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            textContentType="password"
            placeholder="••••••••"
            placeholderTextColor={theme.muted}
            onSubmitEditing={onSubmit}
          />

          {showServer ? (
            <>
              <Text style={[styles.label, { color: theme.muted }]}>SERVER URL</Text>
              <TextInput
                style={inputStyle}
                value={server}
                onChangeText={setServer}
                autoCapitalize="none"
                autoCorrect={false}
                keyboardType="url"
                placeholder="https://app.roobico.com"
                placeholderTextColor={theme.muted}
              />
            </>
          ) : null}

          {error ? <Text style={[styles.error, { color: theme.danger }]}>{error}</Text> : null}

          <Pressable
            style={[styles.button, { backgroundColor: theme.primary, opacity: busy ? 0.7 : 1 }]}
            onPress={onSubmit}
            disabled={busy}
          >
            {busy ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.buttonText}>Sign in</Text>
            )}
          </Pressable>

          <Pressable onPress={() => setShowServer((v) => !v)} style={styles.serverToggle}>
            <Text style={[styles.serverToggleText, { color: theme.muted }]}>
              {showServer ? "Hide server settings" : `Server: ${server || getBaseUrl()}`}
            </Text>
          </Pressable>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  container: { flexGrow: 1, justifyContent: "center", padding: 28 },
  brand: { fontSize: 34, fontWeight: "900", letterSpacing: 2, textAlign: "center" },
  subtitle: { textAlign: "center", marginTop: 4, marginBottom: 32, fontSize: 14 },
  form: { gap: 8, width: "100%", maxWidth: 420, alignSelf: "center" },
  label: { fontSize: 11, fontWeight: "700", letterSpacing: 1, marginTop: 8 },
  input: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 12, fontSize: 15 },
  error: { marginTop: 8, fontSize: 13, fontWeight: "500" },
  button: {
    marginTop: 16,
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: "center",
  },
  buttonText: { color: "#fff", fontSize: 16, fontWeight: "700" },
  serverToggle: { marginTop: 16, alignItems: "center" },
  serverToggleText: { fontSize: 12 },
});
