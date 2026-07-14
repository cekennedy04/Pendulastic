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
