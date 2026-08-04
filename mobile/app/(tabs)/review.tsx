import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Config } from "../../constants/Config";
import { SIZE, T, TYPE } from "../../constants/Theme";
import { useStore } from "../../store";

type TrialStatus =
  | "idle" | "recording" | "pending_review"
  | "approved" | "rejected" | "analyzed" | "error";

const PROCESSING = new Set<TrialStatus>(["idle", "recording"]);

const STATUS_STYLE: Record<string, { bg: string; fg: string; border: string; label: string }> = {
  idle:           { bg: T.borderFaint,  fg: T.label,    border: T.border,        label: "Idle" },
  recording:      { bg: T.dangerBg,     fg: T.danger,   border: T.dangerBorder,  label: "Recording" },
  pending_review: { bg: T.warnBg,       fg: T.warn,     border: T.warnBorder,    label: "Pending review" },
  approved:       { bg: T.successBg,    fg: T.success,  border: T.successBorder, label: "Approved" },
  rejected:       { bg: T.dangerBg,     fg: T.danger,   border: T.dangerBorder,  label: "Rejected" },
  analyzed:       { bg: T.accentLight,  fg: T.accent,   border: "#bfdbfe",       label: "Analyzed" },
  error:          { bg: T.dangerBg,     fg: T.danger,   border: T.dangerBorder,  label: "Error" },
};

export default function ReviewScreen() {
  const { activeTrial, updateTrialStatus } = useStore();
  const [status,   setStatus]   = useState<TrialStatus | null>(activeTrial?.status as TrialStatus ?? null);
  const [deciding, setDeciding] = useState(false);
  const [decError, setDecError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const pollStatus = useCallback(async (trialId: string) => {
    try {
      const res = await fetch(`${Config.API_BASE}/trials/${trialId}/status`);
      if (!res.ok) return;
      const data = await res.json();
      const st = data.status as TrialStatus;
      setStatus(st);
      updateTrialStatus(trialId, st);
      if (!PROCESSING.has(st) && pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    } catch { /* network blip */ }
  }, [updateTrialStatus]);

  useEffect(() => {
    if (!activeTrial) return;
    setStatus(activeTrial.status as TrialStatus);
    setDecError(null);
    if (PROCESSING.has(activeTrial.status as TrialStatus)) {
      pollRef.current = setInterval(() => pollStatus(activeTrial.id), 3000);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [activeTrial, pollStatus]);

  const decide = async (approved: boolean) => {
    if (!activeTrial) return;
    setDeciding(true); setDecError(null);
    try {
      const res = await fetch(`${Config.API_BASE}/trials/${activeTrial.id}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved }),
      });
      if (!res.ok) throw new Error(await res.text());
      const next: TrialStatus = approved ? "approved" : "rejected";
      setStatus(next);
      updateTrialStatus(activeTrial.id, next);
    } catch (e: unknown) {
      setDecError((e as Error).message);
    } finally {
      setDeciding(false);
    }
  };

  if (!activeTrial) {
    return (
      <SafeAreaView style={s.bg} edges={["top"]}>
        <View style={s.header}>
          <Text style={s.title}>Review</Text>
        </View>
        <View style={s.centred}>
          <Text style={s.emptyTitle}>No active trial</Text>
          <Text style={s.emptySub}>Record a trial first, then return here to review it.</Text>
        </View>
      </SafeAreaView>
    );
  }

  const statusMeta = STATUS_STYLE[status ?? "idle"] ?? STATUS_STYLE.idle;

  return (
    <SafeAreaView style={s.bg} edges={["top"]}>
      <View style={s.header}>
        <Text style={s.title}>Review trial</Text>
      </View>

      <ScrollView contentContainerStyle={s.scroll} showsVerticalScrollIndicator={false}>

        {/* Trial metadata */}
        <View style={s.card}>
          <Text style={s.sectionLabel}>TRIAL INFO</Text>
          <MetaRow label="Trial ID"  value={activeTrial.id.slice(0, 8) + "…"} />
          <MetaRow label="HPE model" value={activeTrial.hpe_model} />
          <MetaRow label="Leg side"  value={activeTrial.leg_side} />
          <MetaRow label="Created"   value={new Date(activeTrial.created_at).toLocaleString()} last />
        </View>

        {/* Status */}
        <View style={s.card}>
          <Text style={s.sectionLabel}>STATUS</Text>
          {PROCESSING.has(status ?? "idle") ? (
            <View style={s.processingRow}>
              <ActivityIndicator color={T.accent} />
              <Text style={s.processingText}>Pipeline processing…</Text>
            </View>
          ) : (
            <View style={[s.chip, { backgroundColor: statusMeta.bg, borderColor: statusMeta.border }]}>
              <Text style={[s.chipText, { color: statusMeta.fg }]}>{statusMeta.label}</Text>
            </View>
          )}
        </View>

        {/* Decision controls */}
        {status === "pending_review" && (
          <View style={s.card}>
            <Text style={s.sectionLabel}>DECISION</Text>
            <Text style={s.decisionNote}>
              Verify that the skeleton tracked the joint correctly throughout the swing, then approve or reject.
            </Text>
            {decError && (
              <View style={s.errorBox}>
                <Text style={s.errorText}>{decError}</Text>
              </View>
            )}
            <View style={s.decisionRow}>
              <Pressable
                style={[s.rejectBtn, deciding && s.btnDim]}
                onPress={() => decide(false)}
                disabled={deciding}
              >
                {deciding
                  ? <ActivityIndicator color={T.danger} />
                  : <Text style={s.rejectBtnText}>✕  Reject</Text>
                }
              </Pressable>
              <Pressable
                style={[s.approveBtn, deciding && s.btnDim]}
                onPress={() => decide(true)}
                disabled={deciding}
              >
                {deciding
                  ? <ActivityIndicator color="#fff" />
                  : <Text style={s.approveBtnText}>✓  Approve</Text>
                }
              </Pressable>
            </View>
          </View>
        )}

        {status === "approved" && (
          <View style={[s.card, { borderColor: T.successBorder, backgroundColor: T.successBg }]}>
            <Text style={{ color: T.success, fontSize: TYPE.body, lineHeight: 24, fontWeight: "600" }}>
              Trial approved — open the Analysis tab to view results.
            </Text>
          </View>
        )}

        {status === "rejected" && (
          <View style={[s.card, { borderColor: T.dangerBorder, backgroundColor: T.dangerBg }]}>
            <Text style={{ color: T.danger, fontSize: TYPE.body, lineHeight: 24, fontWeight: "600" }}>
              Trial rejected — record a new trial and return here to review it.
            </Text>
          </View>
        )}

        {status === "error" && (
          <View style={[s.card, { borderColor: T.dangerBorder, backgroundColor: T.dangerBg }]}>
            <Text style={{ color: T.danger, fontSize: TYPE.body, lineHeight: 24, fontWeight: "600" }}>
              The pipeline reported an error.{activeTrial.error ? `\n\n${activeTrial.error}` : ""}
            </Text>
          </View>
        )}

      </ScrollView>
    </SafeAreaView>
  );
}

function MetaRow({ label, value, last }: { label: string; value: string; last?: boolean }) {
  return (
    <View style={[s.metaRow, last && { borderBottomWidth: 0 }]}>
      <Text style={s.metaLabel}>{label}</Text>
      <Text style={s.metaValue}>{value}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  bg:             { flex: 1, backgroundColor: T.bg },
  header:         { paddingHorizontal: 18, paddingVertical: 16 },
  title:          { fontSize: TYPE.title, fontWeight: "800", color: T.text },
  scroll:         { padding: 18, gap: 16, paddingBottom: 44 },

  centred:        { flex: 1, alignItems: "center", justifyContent: "center", padding: 40, gap: 12 },
  emptyTitle:     { fontSize: TYPE.title, fontWeight: "700", color: T.text },
  emptySub:       { fontSize: TYPE.body, color: T.textSub, textAlign: "center" },

  card:           { backgroundColor: T.card, borderRadius: SIZE.cardRadius, borderWidth: 1.5, borderColor: T.border, padding: 20, gap: 12 },
  sectionLabel:   { fontSize: TYPE.label, color: T.label, fontWeight: "700", letterSpacing: 0.9, textTransform: "uppercase" },

  metaRow:        { flexDirection: "row", justifyContent: "space-between", paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: T.borderFaint },
  metaLabel:      { fontSize: TYPE.body, color: T.textSub },
  metaValue:      { fontSize: TYPE.body, color: T.text, fontWeight: "700", textTransform: "capitalize" },

  processingRow:  { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 8 },
  processingText: { color: T.textSub, fontSize: TYPE.body, fontWeight: "600" },

  chip:           { alignSelf: "flex-start", borderRadius: 8, borderWidth: 1.5, paddingHorizontal: 14, paddingVertical: 8 },
  chipText:       { fontSize: TYPE.caption, fontWeight: "700" },

  decisionNote:   { fontSize: TYPE.caption, color: T.textSub, lineHeight: 21 },
  errorBox:       { backgroundColor: T.dangerBg, borderRadius: 8, padding: 12, borderWidth: 1.5, borderColor: T.dangerBorder },
  errorText:      { color: T.danger, fontSize: TYPE.caption, fontWeight: "600" },
  decisionRow:    { flexDirection: "row", gap: 14, marginTop: 6 },
  rejectBtn:      { flex: 1, borderWidth: 2, borderColor: T.dangerBorder, borderRadius: SIZE.btnRadius, padding: 16, alignItems: "center", minHeight: 56, justifyContent: "center" },
  rejectBtnText:  { color: T.danger, fontSize: TYPE.body, fontWeight: "700" },
  approveBtn:     { flex: 1, backgroundColor: T.success, borderRadius: SIZE.btnRadius, padding: 16, alignItems: "center", minHeight: 56, justifyContent: "center" },
  approveBtnText: { color: "#fff", fontSize: TYPE.body, fontWeight: "700" },
  btnDim:         { opacity: 0.4 },
});
