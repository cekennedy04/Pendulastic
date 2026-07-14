import { router } from "expo-router";
import * as ImagePicker from "expo-image-picker";
import React, { useCallback, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { CameraStream } from "../../components/camera/CameraStream";
import { Config } from "../../constants/Config";
import { T } from "../../constants/Theme";
import { useStore } from "../../store";
import type { HpeModel, LegSide } from "../../types";

const MODELS: HpeModel[] = ["mediapipe", "rtmpose", "hrnet", "vitpose"];
const CD_OPTIONS: number[] = [3, 5, 10];

type RecordMode = "live" | "upload";

export default function RecordScreen() {
  const { activeParticipant, setActiveTrial, setAnalysisUnlocked } = useStore();

  const [mode,       setMode]       = useState<RecordMode>("live");
  const [model,      setModel]      = useState<HpeModel>("mediapipe");
  const [legSide,    setLegSide]    = useState<LegSide>("right");
  const [cdSecs,     setCdSecs]     = useState(3);
  const [trialId,    setTrialId]    = useState<string | null>(null);
  const [creating,   setCreating]   = useState(false);
  const [error,      setError]      = useState<string | null>(null);
  const [showConfig, setShowConfig] = useState(true);

  // Upload-mode state
  const [videoUri,   setVideoUri]   = useState<string | null>(null);
  const [videoName,  setVideoName]  = useState<string>("video.mp4");

  // ── Live camera: create trial and open camera ─────────────────────────────

  const createTrialAndStart = useCallback(async () => {
    if (!activeParticipant) { setError("Select a participant first."); return; }
    setCreating(true); setError(null);
    try {
      const res = await fetch(`${Config.API_BASE}/trials/record`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          participant_id: activeParticipant.id,
          leg_side:       legSide,
          hpe_model:      model,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      const trial = await res.json();
      setActiveTrial(trial);
      setTrialId(trial.id);
      setShowConfig(false);
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setCreating(false);
    }
  }, [activeParticipant, legSide, model, setActiveTrial]);

  const handleTrialComplete = useCallback(() => {
    setAnalysisUnlocked(false);
    router.replace("/(tabs)/review");
  }, [setAnalysisUnlocked]);

  // ── Upload mode: pick a video then POST as multipart ──────────────────────

  const pickVideo = async () => {
    setError(null);
    const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (status !== "granted") {
      setError("Photo library access is required to upload a video.");
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["videos"],
      allowsEditing: false,
      quality: 1,
    });
    if (result.canceled) return;
    const asset = result.assets[0];
    setVideoUri(asset.uri);
    setVideoName(asset.fileName ?? "video.mp4");
  };

  const uploadAndSubmit = async () => {
    if (!activeParticipant || !videoUri) return;
    setCreating(true); setError(null);
    try {
      const form = new FormData();
      form.append("participant_id", activeParticipant.id);
      form.append("leg_side",       legSide);
      form.append("hpe_model",      model);
      form.append("video", { uri: videoUri, name: videoName, type: "video/mp4" } as unknown as Blob);

      const res = await fetch(`${Config.API_BASE}/trials/upload`, {
        method: "POST",
        body:   form,
      });
      if (!res.ok) throw new Error(await res.text());
      const trial = await res.json();
      setActiveTrial(trial);
      setAnalysisUnlocked(false);
      router.replace("/(tabs)/review");
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setCreating(false);
    }
  };

  // ── Live camera view ──────────────────────────────────────────────────────

  if (!showConfig && trialId) {
    return (
      <View style={{ flex: 1, backgroundColor: "#000" }}>
        <CameraStream
          trialId={trialId}
          model={model}
          legSide={legSide}
          countdownSecs={cdSecs}
          onTrialComplete={handleTrialComplete}
        />
        <Pressable
          style={s.backBtn}
          onPress={() => { setShowConfig(true); setTrialId(null); }}
        >
          <Text style={s.backBtnText}>✕ Cancel</Text>
        </Pressable>
      </View>
    );
  }

  // ── Config screen ─────────────────────────────────────────────────────────

  return (
    <SafeAreaView style={s.bg} edges={["top"]}>
      <View style={s.header}>
        <Text style={s.title}>New trial</Text>
        {activeParticipant && (
          <View style={s.badge}>
            <Text style={s.badgeText}>
              {activeParticipant.first_name} {activeParticipant.last_name.charAt(0)}.
            </Text>
          </View>
        )}
      </View>

      <ScrollView contentContainerStyle={s.scroll} showsVerticalScrollIndicator={false}>

        {!activeParticipant && (
          <View style={s.warnBox}>
            <Text style={s.warnText}>No participant selected — go to the Patient tab first.</Text>
          </View>
        )}

        {/* Mode */}
        <View style={s.card}>
          <Text style={s.sectionLabel}>INPUT MODE</Text>
          <View style={s.segRow}>
            <Pressable
              style={[s.seg, mode === "live" && s.segActive]}
              onPress={() => { setMode("live"); setVideoUri(null); setError(null); }}
            >
              <Text style={[s.segText, mode === "live" && s.segActiveText]}>📷  Live camera</Text>
            </Pressable>
            <Pressable
              style={[s.seg, mode === "upload" && s.segActive]}
              onPress={() => { setMode("upload"); setError(null); }}
            >
              <Text style={[s.segText, mode === "upload" && s.segActiveText]}>📁  Upload video</Text>
            </Pressable>
          </View>
        </View>

        {/* Affected leg */}
        <View style={s.card}>
          <Text style={s.sectionLabel}>AFFECTED LEG</Text>
          <View style={s.segRow}>
            {(["left", "right"] as LegSide[]).map((side) => (
              <Pressable
                key={side}
                style={[s.seg, legSide === side && s.segActive]}
                onPress={() => setLegSide(side)}
              >
                <Text style={[s.segText, legSide === side && s.segActiveText]}>
                  {side.charAt(0).toUpperCase() + side.slice(1)}
                </Text>
              </Pressable>
            ))}
          </View>
        </View>

        {/* HPE model */}
        <View style={s.card}>
          <Text style={s.sectionLabel}>HPE MODEL</Text>
          <View style={s.segRow}>
            {MODELS.map((m) => (
              <Pressable
                key={m}
                style={[s.seg, model === m && s.segActive]}
                onPress={() => setModel(m)}
              >
                <Text style={[s.segText, model === m && s.segActiveText]}>{m}</Text>
              </Pressable>
            ))}
          </View>
        </View>

        {/* Countdown — live only */}
        {mode === "live" && (
          <View style={s.card}>
            <Text style={s.sectionLabel}>COUNTDOWN</Text>
            <View style={s.segRow}>
              {CD_OPTIONS.map((sec) => (
                <Pressable
                  key={sec}
                  style={[s.seg, cdSecs === sec && s.segActive]}
                  onPress={() => setCdSecs(sec)}
                >
                  <Text style={[s.segText, cdSecs === sec && s.segActiveText]}>{sec} s</Text>
                </Pressable>
              ))}
            </View>
          </View>
        )}

        {/* Upload picker — upload mode only */}
        {mode === "upload" && (
          <View style={s.card}>
            <Text style={s.sectionLabel}>VIDEO FILE</Text>
            <Pressable style={s.pickBtn} onPress={pickVideo}>
              <Text style={s.pickBtnText}>
                {videoUri ? `✓  ${videoName}` : "Choose video from library…"}
              </Text>
            </Pressable>
            {videoUri && (
              <Text style={s.uploadNote}>
                Video selected. Tap "Upload & run pipeline" to submit.
              </Text>
            )}
          </View>
        )}

        {error && (
          <View style={s.errorBox}>
            <Text style={s.errorText}>{error}</Text>
          </View>
        )}

        {/* CTA */}
        {mode === "live" ? (
          <Pressable
            style={[s.cta, (!activeParticipant || creating) && s.ctaDim]}
            onPress={createTrialAndStart}
            disabled={!activeParticipant || creating}
          >
            {creating
              ? <ActivityIndicator color="#fff" />
              : <Text style={s.ctaText}>Open camera</Text>
            }
          </Pressable>
        ) : (
          <Pressable
            style={[s.cta, (!activeParticipant || !videoUri || creating) && s.ctaDim]}
            onPress={uploadAndSubmit}
            disabled={!activeParticipant || !videoUri || creating}
          >
            {creating
              ? (
                <View style={s.uploadingRow}>
                  <ActivityIndicator color="#fff" />
                  <Text style={s.ctaText}>Uploading…</Text>
                </View>
              )
              : <Text style={s.ctaText}>Upload &amp; run pipeline</Text>
            }
          </Pressable>
        )}

      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  bg:             { flex: 1, backgroundColor: T.bg },
  header:         { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 16, paddingVertical: 14 },
  title:          { fontSize: 22, fontWeight: "700", color: T.text },
  badge:          { backgroundColor: T.accentLight, borderRadius: 20, paddingHorizontal: 12, paddingVertical: 6 },
  badgeText:      { fontSize: 12, color: T.accentText, fontWeight: "500" },
  scroll:         { padding: 16, gap: 14, paddingBottom: 40 },

  warnBox:        { backgroundColor: T.warnBg, borderRadius: 12, padding: 14, borderWidth: 1, borderColor: T.warnBorder },
  warnText:       { color: T.warn, fontSize: 13, lineHeight: 18 },

  card:           { backgroundColor: T.card, borderRadius: 14, borderWidth: 1, borderColor: T.border, padding: 16, gap: 12 },
  sectionLabel:   { fontSize: 11, color: T.label, fontWeight: "600", letterSpacing: 0.8, textTransform: "uppercase" },

  segRow:         { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  seg:            { paddingHorizontal: 16, paddingVertical: 10, borderRadius: 10, borderWidth: 1, borderColor: T.border, backgroundColor: T.bg },
  segActive:      { borderColor: T.accent, backgroundColor: T.accentLight },
  segText:        { color: T.textSub, fontSize: 13 },
  segActiveText:  { color: T.accentText, fontWeight: "500" },

  pickBtn:        { backgroundColor: T.bg, borderRadius: 10, borderWidth: 1, borderColor: T.border, padding: 14, alignItems: "center" },
  pickBtnText:    { color: T.accent, fontSize: 14 },
  uploadNote:     { fontSize: 12, color: T.textSub, textAlign: "center" },

  errorBox:       { backgroundColor: T.dangerBg, borderRadius: 10, padding: 12, borderWidth: 1, borderColor: T.dangerBorder },
  errorText:      { color: T.danger, fontSize: 13 },

  cta:            { backgroundColor: T.text, borderRadius: 14, padding: 16, alignItems: "center" },
  ctaDim:         { opacity: 0.35 },
  ctaText:        { color: "#fff", fontSize: 16, fontWeight: "600" },
  uploadingRow:   { flexDirection: "row", alignItems: "center", gap: 10 },

  backBtn:        { position: "absolute", top: 52, right: 16, backgroundColor: "rgba(0,0,0,0.55)", borderRadius: 20, paddingHorizontal: 12, paddingVertical: 6 },
  backBtnText:    { color: "#e2e8f0", fontSize: 12 },
});
