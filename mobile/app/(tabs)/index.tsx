import { router } from "expo-router";
import React from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { T } from "../../constants/Theme";
import { useStore } from "../../store";

const STATUS_DOT: Record<string, string> = {
  idle:           T.label,
  recording:      T.danger,
  pending_review: T.warn,
  approved:       T.success,
  rejected:       T.danger,
  analyzed:       T.accent,
  error:          T.danger,
};

export default function DashboardScreen() {
  const { activeParticipant, activeTrial } = useStore();

  return (
    <SafeAreaView style={s.bg} edges={["top"]}>
      <ScrollView contentContainerStyle={s.scroll} showsVerticalScrollIndicator={false}>

        <View style={s.header}>
          <Text style={s.appName}>Pendulastic</Text>
          <Text style={s.appVersion}>Clinical · v2.1</Text>
        </View>

        {/* Active patient */}
        <Pressable style={s.card} onPress={() => router.push("/(tabs)/participant")}>
          <Text style={s.sectionLabel}>ACTIVE PATIENT</Text>
          {activeParticipant ? (
            <>
              <Text style={s.cardPrimary}>
                {activeParticipant.first_name} {activeParticipant.last_name}
              </Text>
              {activeParticipant.diagnosis
                ? <Text style={s.cardSub}>{activeParticipant.diagnosis}</Text>
                : null}
              {activeParticipant.affected_side && (
                <View style={s.pill}>
                  <Text style={s.pillText}>
                    {activeParticipant.affected_side === "left" ? "Left leg" : "Right leg"}
                  </Text>
                </View>
              )}
            </>
          ) : (
            <Text style={s.cta}>Tap to select a patient →</Text>
          )}
        </Pressable>

        {/* Active trial */}
        <View style={s.card}>
          <Text style={s.sectionLabel}>ACTIVE TRIAL</Text>
          {activeTrial ? (
            <>
              <View style={s.statusRow}>
                <View style={[s.dot, { backgroundColor: STATUS_DOT[activeTrial.status] ?? T.label }]} />
                <Text style={s.cardPrimary}>{activeTrial.status.replace(/_/g, " ")}</Text>
              </View>
              <Text style={s.cardSub}>{activeTrial.hpe_model} · {activeTrial.leg_side} leg</Text>
              <Text style={s.cardSub}>{new Date(activeTrial.created_at).toLocaleString()}</Text>

              {activeTrial.status === "pending_review" && (
                <Pressable style={s.actionBtn} onPress={() => router.push("/(tabs)/review")}>
                  <Text style={s.actionBtnText}>Review trial →</Text>
                </Pressable>
              )}
              {activeTrial.status === "approved" && (
                <Pressable style={s.actionBtn} onPress={() => router.push("/(tabs)/analysis")}>
                  <Text style={s.actionBtnText}>View analysis →</Text>
                </Pressable>
              )}
            </>
          ) : (
            <Text style={s.cardSub}>No active trial — go to Record to begin.</Text>
          )}
        </View>

        {/* Workflow guide */}
        <View style={s.card}>
          <Text style={s.sectionLabel}>WORKFLOW</Text>
          {[
            ["1", "Patient",  "Select or create a participant"],
            ["2", "Record",   "Capture a pendulum swing trial"],
            ["3", "Review",   "Approve the trial before analysis"],
            ["4", "Analysis", "View MAS estimate and PT parameters"],
          ].map(([n, step, desc]) => (
            <View key={step} style={s.stepRow}>
              <View style={s.stepNum}>
                <Text style={s.stepNumText}>{n}</Text>
              </View>
              <View style={{ flex: 1 }}>
                <Text style={s.stepTitle}>{step}</Text>
                <Text style={s.stepDesc}>{desc}</Text>
              </View>
            </View>
          ))}
        </View>

      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  bg:            { flex: 1, backgroundColor: T.bg },
  scroll:        { padding: 16, gap: 14, paddingBottom: 40 },

  header:        { marginBottom: 4 },
  appName:       { fontSize: 26, fontWeight: "700", color: T.text },
  appVersion:    { fontSize: 12, color: T.label, marginTop: 2 },

  card:          { backgroundColor: T.card, borderRadius: 14, borderWidth: 1, borderColor: T.border, padding: 16, gap: 8 },
  sectionLabel:  { fontSize: 11, color: T.label, fontWeight: "600", letterSpacing: 0.8, textTransform: "uppercase" },
  cardPrimary:   { fontSize: 17, fontWeight: "600", color: T.text, textTransform: "capitalize" },
  cardSub:       { fontSize: 13, color: T.textSub },
  cta:           { fontSize: 14, color: T.accent },

  pill:          { alignSelf: "flex-start", backgroundColor: T.accentLight, borderRadius: 6, paddingHorizontal: 10, paddingVertical: 4, marginTop: 2 },
  pillText:      { fontSize: 12, color: T.accentText, fontWeight: "500" },

  statusRow:     { flexDirection: "row", alignItems: "center", gap: 8 },
  dot:           { width: 8, height: 8, borderRadius: 4 },

  actionBtn:     { alignSelf: "flex-start", borderWidth: 1, borderColor: T.accent, borderRadius: 8, paddingHorizontal: 14, paddingVertical: 8, marginTop: 4 },
  actionBtnText: { fontSize: 13, color: T.accent, fontWeight: "500" },

  stepRow:       { flexDirection: "row", gap: 12, alignItems: "flex-start", paddingTop: 8, borderTopWidth: 1, borderTopColor: T.borderFaint },
  stepNum:       { width: 22, height: 22, borderRadius: 11, backgroundColor: T.borderFaint, alignItems: "center", justifyContent: "center", marginTop: 1 },
  stepNumText:   { fontSize: 11, color: T.textSub, fontWeight: "700" },
  stepTitle:     { fontSize: 13, color: T.text, fontWeight: "600" },
  stepDesc:      { fontSize: 12, color: T.textSub, marginTop: 1 },
});
