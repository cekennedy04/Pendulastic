/**
 * app/(tabs)/participant.tsx
 * ==========================
 * Patient detail and selection screen.
 *
 * When a participant is active the framework renders their full clinical
 * profile in an editable form (Identity + Anthropometrics sections).
 * The clinician can save changes via PATCH /api/participants/{id} or switch
 * patients via the list selector.  When no participant is active the list
 * selector is shown immediately so the clinician can pick or create one.
 */

import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Config } from "../../constants/Config";
import { SIZE, T, TYPE } from "../../constants/Theme";
import { useStore } from "../../store";
import type { LegSide, Participant } from "../../types";

async function fetchWithTimeout(
  url: string,
  options: RequestInit = {},
  timeoutMs = 10_000,
): Promise<Response> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: ctrl.signal });
  } catch (e: unknown) {
    if ((e as Error).name === "AbortError") {
      throw new Error(
        `Timed out after ${timeoutMs / 1000}s — is the backend running?\n${Config.API_BASE}`,
      );
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

// ── Participant detail / edit view ────────────────────────────────────────────

export default function ParticipantScreen() {
  const { activeParticipant, setActiveParticipant } = useStore();
  const [showList, setShowList] = useState(!activeParticipant);

  useEffect(() => {
    if (!activeParticipant) setShowList(true);
  }, [activeParticipant]);

  if (showList) {
    return (
      <PatientListView
        onSelect={(p) => {
          setActiveParticipant(p);
          setShowList(false);
        }}
        onBack={activeParticipant ? () => setShowList(false) : undefined}
      />
    );
  }

  return (
    <PatientDetailView
      participant={activeParticipant!}
      onChangePatient={() => setShowList(true)}
      onSaved={(updated) => setActiveParticipant(updated)}
    />
  );
}

// ── Detail / edit form ────────────────────────────────────────────────────────

interface DetailProps {
  participant:     Participant;
  onChangePatient: () => void;
  onSaved:         (p: Participant) => void;
}

function PatientDetailView({ participant, onChangePatient, onSaved }: DetailProps) {
  const [fullName,      setFullName]      = useState(`${participant.first_name} ${participant.last_name}`);
  const [dob,           setDob]           = useState(participant.date_of_birth ?? "");
  const [sex,           setSex]           = useState(participant.sex ?? "unknown");
  const [diagnosis,     setDiagnosis]     = useState(participant.diagnosis ?? "");
  const [icd10,         setIcd10]         = useState(participant.icd10 ?? "");
  const [clinician,     setClinician]     = useState(participant.clinician ?? "");
  const [thigh,         setThigh]         = useState(participant.thigh_length_cm?.toString() ?? "");
  const [shank,         setShank]         = useState(participant.shank_length_cm?.toString() ?? "");
  const [mass,          setMass]          = useState(participant.mass_kg?.toString() ?? "");
  const [affectedSide,  setAffectedSide]  = useState<LegSide>(participant.affected_side ?? "right");
  const [saving,        setSaving]        = useState(false);
  const [error,         setError]         = useState<string | null>(null);
  const [saved,         setSaved]         = useState(false);

  const handleSave = async () => {
    const parts = fullName.trim().split(/\s+/);
    const firstName = parts[0] ?? "";
    const lastName  = parts.slice(1).join(" ");

    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const body: Partial<Participant> = {
        first_name:       firstName,
        last_name:        lastName,
        sex:              sex as Participant["sex"],
        affected_side:    affectedSide,
        ...(dob.trim()       && { date_of_birth:    dob.trim() }),
        ...(diagnosis.trim() && { diagnosis:         diagnosis.trim() }),
        ...(icd10.trim()     && { icd10:             icd10.trim() }),
        ...(clinician.trim() && { clinician:         clinician.trim() }),
        ...(thigh            && { thigh_length_cm:   parseFloat(thigh) }),
        ...(shank            && { shank_length_cm:   parseFloat(shank) }),
        ...(mass             && { mass_kg:           parseFloat(mass) }),
      };
      const res = await fetchWithTimeout(`${Config.API_BASE}/participants/${participant.id}`, {
        method:  "PATCH",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await res.text());
      const updated: Participant = await res.json();
      onSaved(updated);
      setSaved(true);
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const badgeLabel = `${participant.first_name} ${participant.last_name.charAt(0)}.  ·  ${participant.id.slice(0, 8)}`;

  return (
    <SafeAreaView style={s.bg} edges={["top"]}>
      {/* Header */}
      <View style={s.header}>
        <Text style={s.title}>Participant</Text>
        <Pressable style={s.badge} onPress={onChangePatient}>
          <Text style={s.badgeText} numberOfLines={1}>{badgeLabel}</Text>
        </Pressable>
      </View>

      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        keyboardVerticalOffset={80}
      >
        <ScrollView contentContainerStyle={s.scroll} showsVerticalScrollIndicator={false}>

          {/* IDENTITY */}
          <View style={s.card}>
            <Text style={s.sectionLabel}>IDENTITY</Text>

            <FormField label="FULL NAME">
              <TextInput
                style={s.input}
                value={fullName}
                onChangeText={setFullName}
                placeholder="Jane Doe"
                placeholderTextColor={T.placeholder}
                autoCapitalize="words"
              />
            </FormField>

            <FormField label="DATE OF BIRTH">
              <TextInput
                style={s.input}
                value={dob}
                onChangeText={setDob}
                placeholder="YYYY-MM-DD"
                placeholderTextColor={T.placeholder}
                keyboardType="numbers-and-punctuation"
              />
            </FormField>

            <FormField label="SEX">
              <View style={s.segRow}>
                {(["M", "F", "other", "unknown"] as const).map((opt) => (
                  <Pressable
                    key={opt}
                    style={[s.seg, sex === opt && s.segActive]}
                    onPress={() => setSex(opt)}
                  >
                    <Text style={[s.segText, sex === opt && s.segActiveText]}>
                      {opt === "M" ? "Male" : opt === "F" ? "Female" : opt === "other" ? "Other" : "Unknown"}
                    </Text>
                  </Pressable>
                ))}
              </View>
            </FormField>

            <FormField label="DIAGNOSIS">
              <TextInput
                style={s.input}
                value={diagnosis}
                onChangeText={setDiagnosis}
                placeholder="e.g. Stroke, MS, CP"
                placeholderTextColor={T.placeholder}
              />
            </FormField>

            <FormField label="ICD-10">
              <TextInput
                style={s.input}
                value={icd10}
                onChangeText={setIcd10}
                placeholder="e.g. G80.1"
                placeholderTextColor={T.placeholder}
                autoCapitalize="characters"
              />
            </FormField>

            <FormField label="CLINICIAN" last>
              <TextInput
                style={s.input}
                value={clinician}
                onChangeText={setClinician}
                placeholder="Dr. A. Patel"
                placeholderTextColor={T.placeholder}
                autoCapitalize="words"
              />
            </FormField>
          </View>

          {/* ANTHROPOMETRICS */}
          <View style={s.card}>
            <Text style={s.sectionLabel}>ANTHROPOMETRICS</Text>

            <MetricRow
              label="Thigh length"
              value={thigh}
              unit="cm"
              onChange={setThigh}
            />
            <MetricRow
              label="Shank length"
              value={shank}
              unit="cm"
              onChange={setShank}
            />
            <MetricRow
              label="Mass"
              value={mass}
              unit="kg"
              onChange={setMass}
            />

            <View style={s.metricRow}>
              <Text style={s.metricLabel}>Affected side</Text>
              <View style={s.sideToggle}>
                {(["left", "right"] as LegSide[]).map((side) => (
                  <Pressable
                    key={side}
                    style={[s.sidePill, affectedSide === side && s.sidePillActive]}
                    onPress={() => setAffectedSide(side)}
                  >
                    <Text style={[s.sidePillText, affectedSide === side && s.sidePillActiveText]}>
                      {side.charAt(0).toUpperCase() + side.slice(1)}
                    </Text>
                  </Pressable>
                ))}
              </View>
            </View>
          </View>

          {error && (
            <View style={s.errorBox}>
              <Text style={s.errorText}>{error}</Text>
            </View>
          )}

          {saved && (
            <View style={s.successBox}>
              <Text style={s.successText}>Participant saved.</Text>
            </View>
          )}

          <Pressable
            style={[s.saveBtn, saving && s.saveBtnDim]}
            onPress={handleSave}
            disabled={saving}
          >
            {saving
              ? <ActivityIndicator color="#fff" />
              : <Text style={s.saveBtnText}>Save participant</Text>
            }
          </Pressable>

          <Pressable style={s.changeLink} onPress={onChangePatient}>
            <Text style={s.changeLinkText}>Change patient</Text>
          </Pressable>

        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

// ── Patient list selector ─────────────────────────────────────────────────────

interface ListProps {
  onSelect: (p: Participant) => void;
  onBack?:  () => void;
}

function PatientListView({ onSelect, onBack }: ListProps) {
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [loading,      setLoading]      = useState(true);
  const [error,        setError]        = useState<string | null>(null);
  const [query,        setQuery]        = useState("");
  const [showNew,      setShowNew]      = useState(false);

  const fetchAll = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const res = await fetchWithTimeout(`${Config.API_BASE}/participants`);
      if (!res.ok) throw new Error(`Server error ${res.status}`);
      setParticipants(await res.json());
    } catch (e: unknown) { setError((e as Error).message); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const filtered = participants.filter((p) => {
    const q = query.toLowerCase();
    return (
      p.first_name.toLowerCase().includes(q) ||
      p.last_name.toLowerCase().includes(q) ||
      (p.diagnosis ?? "").toLowerCase().includes(q)
    );
  });

  if (showNew) {
    return (
      <NewPatientForm
        onCreated={(p) => { setParticipants((prev) => [p, ...prev]); onSelect(p); }}
        onBack={() => setShowNew(false)}
      />
    );
  }

  return (
    <SafeAreaView style={s.bg} edges={["top"]}>
      <View style={s.header}>
        <Text style={s.title}>Select patient</Text>
        {onBack && (
          <Pressable onPress={onBack}>
            <Text style={s.cancelText}>Cancel</Text>
          </Pressable>
        )}
      </View>

      <View style={s.searchRow}>
        <TextInput
          style={s.searchInput}
          placeholder="Search by name or diagnosis…"
          placeholderTextColor={T.placeholder}
          value={query}
          onChangeText={setQuery}
        />
      </View>

      {loading && <ActivityIndicator color={T.accent} style={{ marginTop: 40 }} />}
      {error && (
        <View style={[s.errorBox, { margin: 20 }]}>
          <Text style={s.errorText}>{error}</Text>
          <Pressable onPress={fetchAll}>
            <Text style={{ color: T.accent, fontSize: TYPE.body, fontWeight: "600", marginTop: 6 }}>Retry</Text>
          </Pressable>
        </View>
      )}

      {!loading && !error && (
        <FlatList
          data={filtered}
          keyExtractor={(p) => p.id}
          contentContainerStyle={{ padding: 16, gap: 10, paddingBottom: 100 }}
          ListEmptyComponent={
            <Text style={{ color: T.label, textAlign: "center", marginTop: 40, fontSize: TYPE.body }}>
              {query ? "No matches." : "No patients yet."}
            </Text>
          }
          renderItem={({ item }) => (
            <Pressable style={s.listRow} onPress={() => onSelect(item)}>
              <View style={{ flex: 1 }}>
                <Text style={s.listName}>{item.first_name} {item.last_name}</Text>
                {item.diagnosis && <Text style={s.listSub}>{item.diagnosis}</Text>}
              </View>
              <Text style={{ color: T.label, fontSize: 22 }}>›</Text>
            </Pressable>
          )}
        />
      )}

      <Pressable style={s.fab} onPress={() => setShowNew(true)}>
        <Text style={s.fabText}>+ New patient</Text>
      </Pressable>
    </SafeAreaView>
  );
}

// ── New patient form ──────────────────────────────────────────────────────────

interface NewPatientFormProps {
  onCreated: (p: Participant) => void;
  onBack:    () => void;
}

function NewPatientForm({ onCreated, onBack }: NewPatientFormProps) {
  const [fullName,     setFullName]     = useState("");
  const [dob,          setDob]          = useState("");
  const [sex,          setSex]          = useState<Participant["sex"]>("unknown");
  const [diagnosis,    setDiagnosis]    = useState("");
  const [icd10,        setIcd10]        = useState("");
  const [clinician,    setClinician]    = useState("");
  const [affectedSide, setAffectedSide] = useState<LegSide>("right");
  const [saving,       setSaving]       = useState(false);
  const [error,        setError]        = useState<string | null>(null);

  const handleSave = async () => {
    const parts     = fullName.trim().split(/\s+/);
    const firstName = parts[0] ?? "";
    const lastName  = parts.slice(1).join(" ");
    if (!firstName || !lastName) { setError("Enter full name (first and last)."); return; }

    setSaving(true); setError(null);
    try {
      const res = await fetchWithTimeout(`${Config.API_BASE}/participants`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({
          first_name:    firstName,
          last_name:     lastName,
          sex,
          affected_side: affectedSide,
          ...(dob.trim()       && { date_of_birth: dob.trim() }),
          ...(diagnosis.trim() && { diagnosis:     diagnosis.trim() }),
          ...(icd10.trim()     && { icd10:         icd10.trim() }),
          ...(clinician.trim() && { clinician:     clinician.trim() }),
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      onCreated(await res.json());
    } catch (e: unknown) { setError((e as Error).message); }
    finally { setSaving(false); }
  };

  return (
    <SafeAreaView style={s.bg} edges={["top"]}>
      <View style={s.header}>
        <Pressable onPress={onBack}>
          <Text style={s.cancelText}>← Back</Text>
        </Pressable>
        <Text style={s.title}>New patient</Text>
        <View style={{ width: 60 }} />
      </View>

      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={s.scroll}>
          <View style={s.card}>
            <Text style={s.sectionLabel}>IDENTITY</Text>
            <FormField label="FULL NAME">
              <TextInput style={s.input} value={fullName} onChangeText={setFullName}
                placeholder="Jane Doe" placeholderTextColor={T.placeholder} autoCapitalize="words" />
            </FormField>
            <FormField label="DATE OF BIRTH">
              <TextInput style={s.input} value={dob} onChangeText={setDob}
                placeholder="YYYY-MM-DD" placeholderTextColor={T.placeholder} keyboardType="numbers-and-punctuation" />
            </FormField>
            <FormField label="SEX">
              <View style={s.segRow}>
                {(["M", "F", "other", "unknown"] as const).map((opt) => (
                  <Pressable key={opt} style={[s.seg, sex === opt && s.segActive]} onPress={() => setSex(opt)}>
                    <Text style={[s.segText, sex === opt && s.segActiveText]}>
                      {opt === "M" ? "Male" : opt === "F" ? "Female" : opt === "other" ? "Other" : "Unknown"}
                    </Text>
                  </Pressable>
                ))}
              </View>
            </FormField>
            <FormField label="DIAGNOSIS">
              <TextInput style={s.input} value={diagnosis} onChangeText={setDiagnosis}
                placeholder="e.g. Stroke, MS, CP" placeholderTextColor={T.placeholder} />
            </FormField>
            <FormField label="ICD-10">
              <TextInput style={s.input} value={icd10} onChangeText={setIcd10}
                placeholder="e.g. G80.1" placeholderTextColor={T.placeholder} autoCapitalize="characters" />
            </FormField>
            <FormField label="CLINICIAN" last>
              <TextInput style={s.input} value={clinician} onChangeText={setClinician}
                placeholder="Dr. A. Patel" placeholderTextColor={T.placeholder} autoCapitalize="words" />
            </FormField>
          </View>

          <View style={s.card}>
            <Text style={s.sectionLabel}>AFFECTED SIDE</Text>
            <View style={[s.metricRow, { borderBottomWidth: 0 }]}>
              <View style={s.sideToggle}>
                {(["left", "right"] as LegSide[]).map((side) => (
                  <Pressable key={side}
                    style={[s.sidePill, affectedSide === side && s.sidePillActive]}
                    onPress={() => setAffectedSide(side)}>
                    <Text style={[s.sidePillText, affectedSide === side && s.sidePillActiveText]}>
                      {side.charAt(0).toUpperCase() + side.slice(1)}
                    </Text>
                  </Pressable>
                ))}
              </View>
            </View>
          </View>

          {error && <View style={s.errorBox}><Text style={s.errorText}>{error}</Text></View>}

          <Pressable style={[s.saveBtn, saving && s.saveBtnDim]} onPress={handleSave} disabled={saving}>
            {saving ? <ActivityIndicator color="#fff" /> : <Text style={s.saveBtnText}>Save participant</Text>}
          </Pressable>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

// ── Shared sub-components ─────────────────────────────────────────────────────

function FormField({ label, last, children }: { label: string; last?: boolean; children: React.ReactNode }) {
  return (
    <View style={[s.field, last && { borderBottomWidth: 0 }]}>
      <Text style={s.fieldLabel}>{label}</Text>
      {children}
    </View>
  );
}

function MetricRow({ label, value, unit, onChange }: { label: string; value: string; unit: string; onChange: (v: string) => void }) {
  return (
    <View style={s.metricRow}>
      <Text style={s.metricLabel}>{label}</Text>
      <View style={s.metricInputRow}>
        <TextInput
          style={s.metricInput}
          value={value}
          onChangeText={onChange}
          keyboardType="decimal-pad"
          placeholder="—"
          placeholderTextColor={T.placeholder}
          textAlign="right"
        />
        <Text style={s.metricUnit}>{unit}</Text>
      </View>
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const s = StyleSheet.create({
  bg:             { flex: 1, backgroundColor: T.bg },
  scroll:         { padding: 18, gap: 16, paddingBottom: 72 },

  header:         { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 18, paddingVertical: 16 },
  title:          { fontSize: TYPE.title, fontWeight: "800", color: T.text },
  cancelText:     { fontSize: TYPE.body, color: T.accent, fontWeight: "600" },

  badge:          { backgroundColor: T.accentLight, borderRadius: 20, paddingHorizontal: 14, paddingVertical: 8, maxWidth: 200, minHeight: 36, justifyContent: "center" },
  badgeText:      { fontSize: TYPE.caption, color: T.accentText, fontWeight: "700" },

  card:           { backgroundColor: T.card, borderRadius: SIZE.cardRadius, borderWidth: 1.5, borderColor: T.border, overflow: "hidden" },
  sectionLabel:   { fontSize: TYPE.label, color: T.label, fontWeight: "700", letterSpacing: 0.9, padding: 18, paddingBottom: 10 },

  field:          { paddingHorizontal: 18, paddingBottom: 16, borderBottomWidth: 1, borderBottomColor: T.borderFaint, marginBottom: 0 },
  fieldLabel:     { fontSize: TYPE.label, color: T.label, fontWeight: "700", letterSpacing: 0.6, marginBottom: 8, textTransform: "uppercase" },
  input:          { backgroundColor: T.inputBg, borderRadius: 10, borderWidth: 1.5, borderColor: T.border, paddingHorizontal: 16, paddingVertical: 14, fontSize: TYPE.body, color: T.text, minHeight: SIZE.touchMin },

  segRow:         { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  seg:            { paddingHorizontal: 16, paddingVertical: 12, borderRadius: 10, borderWidth: 1.5, borderColor: T.border, backgroundColor: T.bg, minHeight: SIZE.touchMin, justifyContent: "center" },
  segActive:      { borderColor: T.accent, backgroundColor: T.accentLight },
  segText:        { fontSize: TYPE.body, color: T.textSub },
  segActiveText:  { color: T.accentText, fontWeight: "700" },

  metricRow:      { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 18, paddingVertical: 16, borderBottomWidth: 1, borderBottomColor: T.borderFaint },
  metricLabel:    { fontSize: TYPE.body, color: T.text },
  metricInputRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  metricInput:    { fontSize: TYPE.bodyLg, fontWeight: "700", color: T.text, minWidth: 56, textAlign: "right" },
  metricUnit:     { fontSize: TYPE.body, color: T.textSub, width: 32 },

  sideToggle:     { flexDirection: "row", gap: 10 },
  sidePill:       { paddingHorizontal: 18, paddingVertical: 10, borderRadius: 10, borderWidth: 1.5, borderColor: T.border, backgroundColor: T.bg, minHeight: 44, justifyContent: "center" },
  sidePillActive: { borderColor: T.accent, backgroundColor: T.accentLight },
  sidePillText:   { fontSize: TYPE.body, color: T.textSub },
  sidePillActiveText: { color: T.accentText, fontWeight: "700" },

  errorBox:       { backgroundColor: "#fef2f2", borderRadius: 10, padding: 14, borderWidth: 1.5, borderColor: "#fecaca" },
  errorText:      { color: "#dc2626", fontSize: TYPE.caption, fontWeight: "600" },
  successBox:     { backgroundColor: T.successBg, borderRadius: 10, padding: 14, borderWidth: 1.5, borderColor: T.successBorder },
  successText:    { color: T.success, fontSize: TYPE.caption, fontWeight: "600" },

  saveBtn:        { backgroundColor: T.text, borderRadius: SIZE.btnRadius, padding: SIZE.btnPaddingV, alignItems: "center", minHeight: 56, justifyContent: "center" },
  saveBtnDim:     { opacity: 0.4 },
  saveBtnText:    { color: "#fff", fontSize: TYPE.button, fontWeight: "700" },

  changeLink:     { alignItems: "center", paddingVertical: 16, minHeight: SIZE.touchMin, justifyContent: "center" },
  changeLinkText: { color: T.accent, fontSize: TYPE.body, fontWeight: "600" },

  searchRow:      { paddingHorizontal: 18, paddingBottom: 12 },
  searchInput:    { backgroundColor: T.card, borderRadius: 12, borderWidth: 1.5, borderColor: T.border, paddingHorizontal: 16, paddingVertical: 14, fontSize: TYPE.body, color: T.text, minHeight: SIZE.touchMin },

  listRow:        { backgroundColor: T.card, borderRadius: 12, padding: 18, flexDirection: "row", alignItems: "center", borderWidth: 1.5, borderColor: T.border, minHeight: SIZE.touchMin },
  listName:       { fontSize: TYPE.bodyLg, fontWeight: "700", color: T.text },
  listSub:        { fontSize: TYPE.caption, color: T.textSub, marginTop: 3 },

  fab:            { position: "absolute", bottom: 28, right: 18, backgroundColor: T.text, borderRadius: SIZE.btnRadius, paddingHorizontal: 24, paddingVertical: 16, minHeight: 56, justifyContent: "center" },
  fabText:        { color: "#fff", fontSize: TYPE.body, fontWeight: "700" },
});
