import type { ImageEffects } from "@/components/slide-editor/types";

export const DEFAULT_IMAGE_EFFECTS = {
  brightness: 100,
  contrast: 100,
  saturation: 100,
  grayscale: 0,
  sepia: 0,
  hue: 0,
  blur: 0,
} as const;

function num(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

/** CSS `filter` value for an image's effects, or "" when nothing is changed. */
export function imageEffectsFilter(effects: unknown): string {
  if (!effects || typeof effects !== "object") return "";
  const e = effects as ImageEffects;
  const parts: string[] = [];
  const brightness = num(e.brightness, 100);
  const contrast = num(e.contrast, 100);
  const saturation = num(e.saturation, 100);
  const grayscale = num(e.grayscale, 0);
  const sepia = num(e.sepia, 0);
  const hue = num(e.hue, 0);
  const blur = num(e.blur, 0);
  if (brightness !== 100) parts.push(`brightness(${(brightness / 100).toFixed(3)})`);
  if (contrast !== 100) parts.push(`contrast(${(contrast / 100).toFixed(3)})`);
  if (saturation !== 100) parts.push(`saturate(${(saturation / 100).toFixed(3)})`);
  if (grayscale > 0) parts.push(`grayscale(${(grayscale / 100).toFixed(3)})`);
  if (sepia > 0) parts.push(`sepia(${(sepia / 100).toFixed(3)})`);
  if (hue !== 0) parts.push(`hue-rotate(${hue}deg)`);
  if (blur > 0) parts.push(`blur(${blur}px)`);
  return parts.join(" ");
}
