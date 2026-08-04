/**
 * constants/Theme.ts
 * ==================
 * Shared light-theme palette for all mobile screens.
 */

export const T = {
  bg:          "#f9fafb",
  card:        "#ffffff",
  border:      "#e5e7eb",
  borderFaint: "#f3f4f6",

  text:        "#111827",
  textSub:     "#6b7280",
  label:       "#9ca3af",   // ALL-CAPS section labels

  accent:      "#3b82f6",
  accentLight: "#eff6ff",
  accentText:  "#1d4ed8",

  inputBg:     "#ffffff",
  placeholder: "#d1d5db",

  success:     "#16a34a",
  successBg:   "#f0fdf4",
  successBorder:"#bbf7d0",

  danger:      "#dc2626",
  dangerBg:    "#fef2f2",
  dangerBorder:"#fecaca",

  warn:        "#d97706",
  warnBg:      "#fffbeb",
  warnBorder:  "#fde68a",
} as const;

/**
 * Typography scale — sized up from the original for a clinical-instrument
 * feel: readable at arm's length, legible for clinicians wearing gloves or
 * working under variable lighting.
 */
export const TYPE = {
  micro:    13,  // pills, badges, tiny meta text
  label:    13,  // ALL-CAPS section labels
  caption:  14,  // secondary/supporting text
  body:     17,  // primary reading text, inputs
  bodyLg:   18,  // emphasized body / list primaries
  button:   18,  // button labels
  title:    24,  // screen titles
  heading:  28,  // dashboard app name
  display:  40,  // lock icon / large glyphs
  score:    88,  // headline MAS score
} as const;

/** Shared control sizing — 48px is the WCAG/clinical-device minimum touch target. */
export const SIZE = {
  touchMin:     48,
  btnRadius:    12,
  cardRadius:   16,
  btnPaddingV:  18,
  btnPaddingH:  22,
} as const;
