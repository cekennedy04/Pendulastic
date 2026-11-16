import React, { useCallback, useEffect, useState } from "react";
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

interface PTParameters {
  R2n:              number;
  N:                number;
  phi_max_ratio:    number;
  omega_max_n:      number;
  omega_peak_deg_s: number;
  f:                number;
  area_ratio:       number;
}

interface FidelityReport {
  passed:          boolean;
  area_ratio_warn: boolean;
  spasticity_type: string;
  A0_deg:          number;
  neutral_deg:     number;
}

interface AnalysisResult {
  trial_id:     string;
  mas_estimate: number;
  pt_score:     number;
  parameters:   PTParameters;
  fidelity:     FidelityReport;
  time_series:  { t: number[]; angle: number[]; t_r: number[]; phi: number[] };
}

const MAS_LABEL: Record<number, string> = {
  0: "No increase in tone",
  1: "Slight increase",
  2: "More marked increase",
  3: "Considerable increase",
  4: "Limb rigid in flexion or extension",
};

export default function AnalysisScreen() {
  const { analysisUnlocked, activeTrial } = useStore();
  const [result,  setResult]  = useState<AnalysisResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);

  const fetchAnalysis = useCallback(async () => {
    if (!activeTrial) return;
    setLoading(true); setError(null);
    try {
      const res = await fetch(`${Config.API_BASE}/trials/${activeTrial.id}/analysis`);
      if (!res.ok) throw new Error(await res.text());
      setResult(await res.json());
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [activeTrial]);

  useEffect(() => {
    if (analysisUnlocked && activeTrial) fetchAnalysis();
  }, [analysisUnlocked, activeTrial, fetchAnalysis]);

  if (!analysisUnlocked) {
    return (
      <SafeAreaView style={s.bg} edges={["top"]}>
        <View style={s.header}>
          <Text style={s.title}>Analysis</Text>
        </View>
        <View style={s.centred}>
          <Text style={s.lockIcon}>🔒</Text>
          <Text style={s.gateTitle}>Analysis locked</Text>
          <Text style={s.gateSub}>Approve a trial on the Review tab to unlock results.</Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={s.bg} edges={["top"]}>
      <View style={s.header}>
        <Text style={s.title}>Analysis results</Text>
      </View>

      <ScrollView contentContainerStyle={s.scroll} showsVerticalScrollIndicator={false}>

        {loading && <ActivityIndicator color={T.accent} style={{ marginTop: 40 }} />}

        {error && (
          <View style={s.errorBox}>
            <Text style={s.errorText}>{error}</Text>
            <Pressable onPress={fetchAnalysis}>
              <Text style={{ color: T.accent, fontSize: TYPE.body, fontWeight: "600", marginTop: 8 }}>Retry</Text>
            </Pressable>
          </View>
        )}

        {result && (
          <>
            {/* Fidelity banner */}
            <View style={[
              s.fidelityBanner,
              result.fidelity.passed
                ? { backgroundColor: T.successBg, borderColor: T.successBorder }
                : { backgroundColor: T.dangerBg,  borderColor: T.dangerBorder },
            ]}>
              <Text style={[s.fidelityText, { color: result.fidelity.passed ? T.success : T.danger }]}>
                {result.fidelity.passed ? "✓ Data fidelity: PASS" : "⚠ Data fidelity: FAIL"}
                {"  ·  "}
                {result.fidelity.spasticity_type}
              </Text>
            </View>

            {/* MAS estimate */}
            <View style={s.masCard}>
              <Text style={s.sectionLabel}>ESTIMATED MAS</Text>
              <Text style={s.masScore}>{result.mas_estimate}</Text>
              <Text style={s.masDesc}>
                {MAS_LABEL[Math.round(result.mas_estimate)] ?? "—"}
              </Text>
              <View style={s.ptRow}>
                <Text style={s.ptLabel}>PT score</Text>
                <Text style={s.ptValue}>{result.pt_score.toFixed(3)}</Text>
              </View>
            </View>

            {/* Popovic parameters */}
            <View style={s.card}>
              <Text style={s.sectionLabel}>POPOVIC PARAMETERS</Text>
              {([
                ["R₂ₙ (norm. amplitude)", result.parameters.R2n.toFixed(3)],
                ["N (swing cycles)",      result.parameters.N.toFixed(1)],
                ["ω_max ratio",           result.parameters.phi_max_ratio.toFixed(3)],
                ["ω_max_n",               result.parameters.omega_max_n.toFixed(2)],
                ["ω_peak (°/s)",          result.parameters.omega_peak_deg_s.toFixed(0)],
                ["f (Hz)",                result.parameters.f.toFixed(2)],
                ["Area ratio",            result.parameters.area_ratio.toFixed(3)],
              ] as [string, string][]).map(([label, val]) => (
                <ParamRow key={label} label={label} value={val} />
              ))}
            </View>

            {/* Geometry */}
            <View style={s.card}>
              <Text style={s.sectionLabel}>PENDULUM GEOMETRY</Text>
              <ParamRow label="A₀ (initial angle)" value={`${result.fidelity.A0_deg.toFixed(1)}°`} />
              <ParamRow label="Neutral angle"       value={`${result.fidelity.neutral_deg.toFixed(1)}°`} last />
            </View>

            {/* Time-series summary */}
            {result.time_series?.t?.length > 0 && (
              <View style={s.card}>
                <Text style={s.sectionLabel}>TIME SERIES SUMMARY</Text>
                <ParamRow
                  label="Duration"
                  value={`${result.time_series.t[result.time_series.t.length - 1].toFixed(1)} s`}
                />
                <ParamRow
                  label="Frames captured"
                  value={String(result.time_series.t.length)}
                />
                <ParamRow
                  label="Peak knee angle"
                  value={`${Math.max(...result.time_series.angle).toFixed(1)}°`}
                  last
                />
              </View>
            )}
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

function ParamRow({ label, value, last }: { label: string; value: string; last?: boolean }) {
  return (
    <View style={[s.paramRow, last && { borderBottomWidth: 0 }]}>
      <Text style={s.paramLabel}>{label}</Text>
      <Text style={s.paramValue}>{value}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  bg:             { flex: 1, backgroundColor: T.bg },
  header:         { paddingHorizontal: 18, paddingVertical: 16 },
  title:          { fontSize: TYPE.title, fontWeight: "800", color: T.text },
  scroll:         { padding: 18, gap: 16, paddingBottom: 44 },

  centred:        { flex: 1, alignItems: "center", justifyContent: "center", padding: 40, gap: 14 },
  lockIcon:       { fontSize: TYPE.display },
  gateTitle:      { fontSize: TYPE.title, fontWeight: "700", color: T.text, textAlign: "center" },
  gateSub:        { fontSize: TYPE.body, color: T.textSub, textAlign: "center", lineHeight: 24 },

  errorBox:       { backgroundColor: T.dangerBg, borderRadius: 12, padding: 16, borderWidth: 1.5, borderColor: T.dangerBorder },
  errorText:      { color: T.danger, fontSize: TYPE.caption, fontWeight: "600" },

  fidelityBanner: { borderRadius: 12, borderWidth: 1.5, padding: 14 },
  fidelityText:   { fontSize: TYPE.body, fontWeight: "700" },

  masCard:        { backgroundColor: T.card, borderRadius: SIZE.cardRadius, borderWidth: 1.5, borderColor: T.border, padding: 24, alignItems: "center", gap: 6 },
  sectionLabel:   { fontSize: TYPE.label, color: T.label, fontWeight: "700", letterSpacing: 0.9, textTransform: "uppercase", alignSelf: "flex-start" },
  masScore:       { fontSize: TYPE.score, fontWeight: "800", color: T.accent, lineHeight: 96 },
  masDesc:        { fontSize: TYPE.body, color: T.textSub, textAlign: "center", fontWeight: "600" },
  ptRow:          { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 10, padding: 14, backgroundColor: T.bg, borderRadius: 10, width: "100%" },
  ptLabel:        { fontSize: TYPE.caption, color: T.textSub, flex: 1 },
  ptValue:        { fontSize: TYPE.body, fontWeight: "700", color: T.text },

  card:           { backgroundColor: T.card, borderRadius: SIZE.cardRadius, borderWidth: 1.5, borderColor: T.border, padding: 18, gap: 0 },
  paramRow:       { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingVertical: 13, borderBottomWidth: 1, borderBottomColor: T.borderFaint },
  paramLabel:     { fontSize: TYPE.caption, color: T.textSub, flex: 1 },
  paramValue:     { fontSize: TYPE.caption, color: T.text, fontWeight: "700" },
});
