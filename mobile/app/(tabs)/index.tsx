import { router } from "expo-router";
import React from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { SIZE, T, TYPE } from "../../constants/Theme";
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
  scroll:        { padding: 18, gap: 16, paddingBottom: 44 },

  header:        { marginBottom: 6 },
  appName:       { fontSize: TYPE.heading, fontWeight: "800", color: T.text, letterSpacing: 0.2 },
  appVersion:    { fontSize: TYPE.caption, color: T.label, marginTop: 4, fontWeight: "600", letterSpacing: 0.4 },

  card:          { backgroundColor: T.card, borderRadius: SIZE.cardRadius, borderWidth: 1.5, borderColor: T.border, padding: 20, gap: 10 },
  sectionLabel:  { fontSize: TYPE.label, color: T.label, fontWeight: "700", letterSpacing: 0.9, textTransform: "uppercase" },
  cardPrimary:   { fontSize: TYPE.bodyLg, fontWeight: "700", color: T.text, textTransform: "capitalize" },
  cardSub:       { fontSize: TYPE.caption, color: T.textSub },
  cta:           { fontSize: TYPE.body, color: T.accent, fontWeight: "600" },

  pill:          { alignSelf: "flex-start", backgroundColor: T.accentLight, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 6, marginTop: 4 },
  pillText:      { fontSize: TYPE.micro, color: T.accentText, fontWeight: "700" },

  statusRow:     { flexDirection: "row", alignItems: "center", gap: 10 },
  dot:           { width: 10, height: 10, borderRadius: 5 },

  actionBtn:     { alignSelf: "flex-start", minHeight: SIZE.touchMin, justifyContent: "center", borderWidth: 2, borderColor: T.accent, borderRadius: SIZE.btnRadius, paddingHorizontal: 18, paddingVertical: 10, marginTop: 6 },
  actionBtnText: { fontSize: TYPE.body, color: T.accent, fontWeight: "700" },

  stepRow:       { flexDirection: "row", gap: 14, alignItems: "flex-start", paddingTop: 10, borderTopWidth: 1, borderTopColor: T.borderFaint },
  stepNum:       { width: 28, height: 28, borderRadius: 14, backgroundColor: T.borderFaint, alignItems: "center", justifyContent: "center", marginTop: 1 },
  stepNumText:   { fontSize: TYPE.caption, color: T.textSub, fontWeight: "800" },
  stepTitle:     { fontSize: TYPE.body, color: T.text, fontWeight: "700" },
  stepDesc:      { fontSize: TYPE.caption, color: T.textSub, marginTop: 2 },
});
