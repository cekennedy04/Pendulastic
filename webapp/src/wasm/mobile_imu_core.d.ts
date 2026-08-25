/* tslint:disable */
/* eslint-disable */

export class WasmSession {
    free(): void;
    [Symbol.dispose](): void;
    calm_s(): number;
    drift_deg(): number;
    /**
     * JSON of the full `PtParams` payload, or `undefined` when unscorable.
     * Returning `undefined` rather than throwing keeps `TrialError` a value
     * the UI branches on, per KTD7 (never a panic across the boundary).
     */
    finish(): string | undefined;
    constructor(beta: number, ema_alpha: number);
    /**
     * Flat batch, 7 doubles per sample: `[t_ms, ax, ay, az, gx, gy, gz]`.
     * Accel is pushed before gyro for each sample, matching the ordering
     * contract the whole pipeline depends on. Gyro must already be rad/s.
     */
    push_batch(buf: Float64Array): void;
    sample_count(): number;
    /**
     * 0 Moving, 1 Holding, 2 Ready, 3 Released.
     */
    state_code(): number;
}

export type InitInput = RequestInfo | URL | Response | BufferSource | WebAssembly.Module;

export interface InitOutput {
    readonly memory: WebAssembly.Memory;
    readonly __wbg_wasmsession_free: (a: number, b: number) => void;
    readonly wasmsession_calm_s: (a: number) => number;
    readonly wasmsession_drift_deg: (a: number) => number;
    readonly wasmsession_finish: (a: number) => [number, number];
    readonly wasmsession_new: (a: number, b: number) => number;
    readonly wasmsession_push_batch: (a: number, b: number, c: number) => void;
    readonly wasmsession_sample_count: (a: number) => number;
    readonly wasmsession_state_code: (a: number) => number;
    readonly __wbindgen_externrefs: WebAssembly.Table;
    readonly __wbindgen_free: (a: number, b: number, c: number) => void;
    readonly __wbindgen_malloc: (a: number, b: number) => number;
    readonly __wbindgen_start: () => void;
}

export type SyncInitInput = BufferSource | WebAssembly.Module;

/**
 * Instantiates the given `module`, which can either be bytes or
 * a precompiled `WebAssembly.Module`.
 *
 * @param {{ module: SyncInitInput }} module - Passing `SyncInitInput` directly is deprecated.
 *
 * @returns {InitOutput}
 */
export function initSync(module: { module: SyncInitInput } | SyncInitInput): InitOutput;

/**
 * If `module_or_path` is {RequestInfo} or {URL}, makes a request and
 * for everything else, calls `WebAssembly.instantiate` directly.
 *
 * @param {{ module_or_path: InitInput | Promise<InitInput> }} module_or_path - Passing `InitInput` directly is deprecated.
 *
 * @returns {Promise<InitOutput>}
 */
export default function __wbg_init (module_or_path?: { module_or_path: InitInput | Promise<InitInput> } | InitInput | Promise<InitInput>): Promise<InitOutput>;
