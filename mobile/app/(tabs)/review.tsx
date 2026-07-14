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
import { T } from "../../constants/Theme";
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
            <Text style={{ color: T.success, fontSize: 14, lineHeight: 20 }}>
              Trial approved — open the Analysis tab to view results.
            </Text>
          </View>
        )}

        {status === "rejected" && (
          <View style={[s.card, { borderColor: T.dangerBorder, backgroundColor: T.dangerBg }]}>
            <Text style={{ color: T.danger, fontSize: 14, lineHeight: 20 }}>
              Trial rejected — record a new trial and return here to review it.
            </Text>
          </View>
        )}

        {status === "error" && (
          <View style={[s.card, { borderColor: T.dangerBorder, backgroundColor: T.dangerBg }]}>
            <Text style={{ color: T.danger, fontSize: 14, lineHeight: 20 }}>
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
  header:         { paddingHorizontal: 16, paddingVertical: 14 },
  title:          { fontSize: 22, fontWeight: "700", color: T.text },
  scroll:         { padding: 16, gap: 14, paddingBottom: 40 },

  centred:        { flex: 1, alignItems: "center", justifyContent: "center", padding: 40, gap: 10 },
  emptyTitle:     { fontSize: 17, fontWeight: "600", color: T.text },
  emptySub:       { fontSize: 14, color: T.textSub, textAlign: "center" },

  card:           { backgroundColor: T.card, borderRadius: 14, borderWidth: 1, borderColor: T.border, padding: 16, gap: 10 },
  sectionLabel:   { fontSize: 11, color: T.label, fontWeight: "600", letterSpacing: 0.8, textTransform: "uppercase" },

  metaRow:        { flexDirection: "row", justifyContent: "space-between", paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: T.borderFaint },
  metaLabel:      { fontSize: 14, color: T.textSub },
  metaValue:      { fontSize: 14, color: T.text, fontWeight: "500", textTransform: "capitalize" },

  processingRow:  { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 6 },
  processingText: { color: T.textSub, fontSize: 14 },

  chip:           { alignSelf: "flex-start", borderRadius: 8, borderWidth: 1, paddingHorizontal: 12, paddingVertical: 6 },
  chipText:       { fontSize: 13, fontWeight: "600" },

  decisionNote:   { fontSize: 13, color: T.textSub, lineHeight: 19 },
  errorBox:       { backgroundColor: T.dangerBg, borderRadius: 8, padding: 10, borderWidth: 1, borderColor: T.dangerBorder },
  errorText:      { color: T.danger, fontSize: 13 },
  decisionRow:    { flexDirection: "row", gap: 12, marginTop: 4 },
  rejectBtn:      { flex: 1, borderWidth: 1, borderColor: T.dangerBorder, borderRadius: 10, padding: 14, alignItems: "center" },
  rejectBtnText:  { color: T.danger, fontSize: 14, fontWeight: "500" },
  approveBtn:     { flex: 1, backgroundColor: T.success, borderRadius: 10, padding: 14, alignItems: "center" },
  approveBtnText: { color: "#fff", fontSize: 14, fontWeight: "600" },
  btnDim:         { opacity: 0.4 },
});
