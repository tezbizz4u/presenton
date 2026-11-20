"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Check, Palette } from "lucide-react";

import {
  ThemePresetApi,
  type ThemePresetSummary,
} from "@/app/(presentation-generator)/services/api/theme-presets";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

const MODES = ["Light", "Soft", "Dark", "Vivid"];
const TRENDS = [
  "Pantone 2026",
  "Earthy",
  "Warm neutral",
  "Natural green",
  "Healthcare calm",
  "Deep purple",
  "Bold maximalist",
  "Retro nostalgic",
  "Sober business",
];
const PAGE_SIZE = 48;

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selectedId?: string | null;
  onSelect: (preset: ThemePresetSummary | null) => void | Promise<void>;
  /** Show a "Use template default" option. */
  allowReset?: boolean;
};

export const ThemePresetPicker = ({
  open,
  onOpenChange,
  selectedId = null,
  onSelect,
  allowReset = true,
}: Props) => {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState("");
  const [trend, setTrend] = useState("");
  const [items, setItems] = useState<ThemePresetSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const requestId = useRef(0);

  const load = useCallback(
    async (offset: number) => {
      const id = ++requestId.current;
      setLoading(true);
      setFailed(false);
      try {
        const result = await ThemePresetApi.list({ q, mode, trend, offset, limit: PAGE_SIZE });
        if (id !== requestId.current) return;
        setTotal(result.total);
        setItems((prev) => (offset === 0 ? result.items : [...prev, ...result.items]));
      } catch {
        if (id === requestId.current) setFailed(true);
      } finally {
        if (id === requestId.current) setLoading(false);
      }
    },
    [q, mode, trend],
  );

  useEffect(() => {
    if (!open) return;
    const timer = setTimeout(() => void load(0), 200);
    return () => clearTimeout(timer);
  }, [open, load]);

  const choose = async (preset: ThemePresetSummary | null) => {
    await onSelect(preset);
    onOpenChange(false);
  };

  const select = "rounded-md border border-[#EDEEEF] bg-white px-2 py-1.5 text-sm";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Theme library</DialogTitle>
          <DialogDescription>
            2026-trend colour and font themes. Works with any template.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-wrap gap-2">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search (e.g. teal, plum, Figtree)"
            className={`${select} min-w-[200px] flex-1`}
          />
          <select value={mode} onChange={(e) => setMode(e.target.value)} className={select}>
            <option value="">All modes</option>
            {MODES.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
          <select value={trend} onChange={(e) => setTrend(e.target.value)} className={select}>
            <option value="">All trends</option>
            {TRENDS.map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
          {allowReset && (
            <button
              type="button"
              className={`${select} hover:bg-[#F8F8FA]`}
              onClick={() => void choose(null)}
            >
              Template default
            </button>
          )}
        </div>
        <div className="max-h-[55vh] overflow-y-auto pr-1">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {items.map((preset) => (
              <button
                key={preset.id}
                type="button"
                onClick={() => void choose(preset)}
                className={`rounded-lg border p-2 text-left transition-colors hover:bg-[#F8F8FA] ${
                  selectedId === preset.id ? "border-[#7A5AF8]" : "border-[#EDEEEF]"
                }`}
              >
                <div className="flex h-9 overflow-hidden rounded-md border border-[#EDEEEF]">
                  {preset.swatches.map((color, i) => (
                    <span key={i} className="flex-1" style={{ background: color }} />
                  ))}
                </div>
                <p className="mt-1.5 flex items-center gap-1 truncate text-xs font-medium text-[#191919]">
                  {selectedId === preset.id && <Check className="h-3 w-3 shrink-0" />}
                  {preset.name}
                </p>
                <p className="truncate text-[11px] text-[#808080]">{preset.trend}</p>
              </button>
            ))}
          </div>
          {failed && <p className="py-4 text-center text-sm text-[#808080]">Could not load themes.</p>}
          {!loading && !failed && items.length === 0 && (
            <p className="py-4 text-center text-sm text-[#808080]">No themes match.</p>
          )}
          {items.length < total && (
            <button
              type="button"
              disabled={loading}
              onClick={() => void load(items.length)}
              className="mx-auto mt-3 block rounded-md border border-[#EDEEEF] px-4 py-1.5 text-sm hover:bg-[#F8F8FA] disabled:opacity-60"
            >
              {loading ? "Loading..." : `Show more (${total - items.length} left)`}
            </button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};

export const ThemePresetButton = ({
  label,
  onClick,
  className = "",
}: {
  label: string;
  onClick: () => void;
  className?: string;
}) => (
  <button
    type="button"
    onClick={onClick}
    className={`flex items-center gap-1.5 rounded-full border border-[#EDEEEF] bg-white px-3 py-1.5 text-sm font-syne text-[#191919] hover:bg-[#F8F8FA] ${className}`}
  >
    <Palette className="h-4 w-4" /> {label}
  </button>
);
