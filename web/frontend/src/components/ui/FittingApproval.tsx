import React from "react";

interface Props {
  onApprove: () => void;
  onReject:  () => void;
  busy:      boolean;
}

export default function FittingApproval({ onApprove, onReject, busy }: Props) {
  return (
    <div className="card flex flex-col gap-3">
      <h3 className="text-sm font-semibold text-slate-200">
        Does this fitting look correct?
      </h3>
      <p className="text-xs text-slate-400">
        Verify that the skeleton overlay follows the leg correctly before approving.
        Adjust the hip, knee, and ankle markers below if needed, then approve.
      </p>
      <div className="flex gap-3 pt-1">
        <button
          className="btn-primary flex-1"
          onClick={onApprove}
          disabled={busy}
        >
          ✓ Yes — Approve
        </button>
        <button
          className="btn-danger flex-1"
          onClick={onReject}
          disabled={busy}
        >
          ✗ No — Reject
        </button>
      </div>
    </div>
  );
}
