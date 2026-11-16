import React, { useEffect, useState } from "react";
import { api } from "../../api/client";
import { useStore } from "../../store";
import type { Participant, Sex, LegSide } from "../../types";

const BLANK: Omit<Participant, "id"> = {
  first_name: "", last_name: "", date_of_birth: "", sex: "unknown",
  diagnosis: "", icd10: "", clinician: "", thigh_length_cm: undefined,
  shank_length_cm: undefined, mass_kg: undefined, height_cm: undefined,
  affected_side: "left", notes: "",
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="label">{label}</label>
      {children}
    </div>
  );
}

function Input({
  value, onChange, type = "text", placeholder,
}: { value: string | number | undefined; onChange: (v: string) => void; type?: string; placeholder?: string }) {
  return (
    <input
      type={type}
      value={value ?? ""}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      className="bg-surface border border-surface-border rounded-lg px-3 py-1.5
                 text-sm text-slate-100 focus:outline-none focus:border-brand
                 placeholder:text-slate-600"
    />
  );
}

function Select<T extends string>({
  value, onChange, options,
}: { value: T; onChange: (v: T) => void; options: { value: T; label: string }[] }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value as T)}
      className="bg-surface border border-surface-border rounded-lg px-3 py-1.5
                 text-sm text-slate-100 focus:outline-none focus:border-brand"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
}

export default function Participant() {
  const { setActiveParticipant, activeParticipant, setParticipants, participants } = useStore();
  const [form, setForm] = useState<Omit<Participant, "id">>(BLANK);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // Load list once
  useEffect(() => {
    api.listParticipants().then(setParticipants).catch(() => null);
  }, []);

  // Populate form when editing existing
  useEffect(() => {
    if (activeParticipant) {
      const { id: _id, ...rest } = activeParticipant;
      setForm(rest as typeof BLANK);
    } else {
      setForm(BLANK);
    }
  }, [activeParticipant]);

  const set = <K extends keyof typeof BLANK>(k: K, v: (typeof BLANK)[K]) =>
    setForm((f) => ({ ...f, [k]: v }));

  const save = async () => {
    setSaving(true); setMsg(null);
    try {
      if (activeParticipant) {
        const updated = await api.patchParticipant(activeParticipant.id, form);
        setActiveParticipant(updated);
        setMsg({ ok: true, text: "Participant updated." });
      } else {
        const created = await api.createParticipant(form);
        setActiveParticipant(created);
        setMsg({ ok: true, text: `Participant created — ID ${created.id}` });
      }
      api.listParticipants().then(setParticipants).catch(() => null);
    } catch (e: unknown) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="p-6 flex gap-6 max-w-5xl">
      {/* participant list */}
      <div className="w-56 card flex flex-col gap-2 h-fit">
        <div className="flex items-center justify-between">
          <span className="label">Patients</span>
          <button
            className="text-brand text-xs hover:underline"
            onClick={() => setActiveParticipant(null)}
          >+ New</button>
        </div>
        {participants.map((p) => (
          <button
            key={p.id}
            onClick={() => setActiveParticipant(p)}
            className={[
              "text-left px-2 py-1.5 rounded-lg text-sm transition-colors",
              activeParticipant?.id === p.id
                ? "bg-brand text-white"
                : "text-slate-300 hover:bg-surface-border",
            ].join(" ")}
          >
            {p.first_name} {p.last_name}
          </button>
        ))}
      </div>

      {/* form */}
      <div className="flex-1 card flex flex-col gap-5">
        <h2 className="text-sm font-semibold text-slate-200">
          {activeParticipant ? "Edit Participant" : "New Participant"}
        </h2>

        <div className="grid grid-cols-2 gap-4">
          <Field label="First Name">
            <Input value={form.first_name} onChange={(v) => set("first_name", v)} />
          </Field>
          <Field label="Last Name">
            <Input value={form.last_name} onChange={(v) => set("last_name", v)} />
          </Field>
          <Field label="Date of Birth">
            <Input type="date" value={form.date_of_birth} onChange={(v) => set("date_of_birth", v)} />
          </Field>
          <Field label="Sex">
            <Select<Sex>
              value={form.sex ?? "unknown"}
              onChange={(v) => set("sex", v)}
              options={[
                { value: "M",       label: "Male"    },
                { value: "F",       label: "Female"  },
                { value: "other",   label: "Other"   },
                { value: "unknown", label: "Unknown" },
              ]}
            />
          </Field>
          <Field label="Diagnosis">
            <Input value={form.diagnosis} onChange={(v) => set("diagnosis", v)} placeholder="e.g. Cerebral palsy" />
          </Field>
          <Field label="ICD-10 Code">
            <Input value={form.icd10} onChange={(v) => set("icd10", v)} placeholder="e.g. G80.1" />
          </Field>
          <Field label="Clinician">
            <Input value={form.clinician} onChange={(v) => set("clinician", v)} />
          </Field>
          <Field label="Affected Side">
            <Select<LegSide>
              value={form.affected_side ?? "left"}
              onChange={(v) => set("affected_side", v)}
              options={[
                { value: "left",  label: "Left"  },
                { value: "right", label: "Right" },
              ]}
            />
          </Field>
        </div>

        <h3 className="text-xs text-slate-400 uppercase tracking-widest">Anthropometrics</h3>
        <div className="grid grid-cols-4 gap-4">
          {(
            [
              ["thigh_length_cm", "Thigh (cm)"],
              ["shank_length_cm", "Shank (cm)"],
              ["mass_kg",         "Mass (kg) "],
              ["height_cm",       "Height (cm)"],
            ] as const
          ).map(([k, lbl]) => (
            <Field key={k} label={lbl}>
              <Input
                type="number"
                value={form[k as keyof typeof form] as number | undefined}
                onChange={(v) => set(k as keyof typeof BLANK, v ? parseFloat(v) : undefined as never)}
              />
            </Field>
          ))}
        </div>

        <Field label="Notes">
          <textarea
            value={form.notes ?? ""}
            onChange={(e) => set("notes", e.target.value)}
            rows={3}
            className="bg-surface border border-surface-border rounded-lg px-3 py-2
                       text-sm text-slate-100 resize-none focus:outline-none focus:border-brand"
          />
        </Field>

        <div className="flex items-center gap-3">
          <button className="btn-primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save Participant"}
          </button>
          {msg && (
            <span className={`text-sm ${msg.ok ? "text-green-400" : "text-red-400"}`}>
              {msg.text}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
