"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Globe2, ImageIcon, KeyRound, Loader2, Search, Sparkles, Upload, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import { ImagesApi } from "@/app/(presentation-generator)/services/api/images";
import {
  ImagesPlusApi,
  type AiOptions,
  type ImageSource,
  type KeysResponse,
  type Orientation,
  type PlusImage,
} from "@/app/(presentation-generator)/services/api/images-plus";
import { ImageSourcesKeys } from "@/components/image-sources/ImageSourcesKeys";
import { notify } from "@/components/ui/sonner";
import { cn } from "@/lib/utils";
import { resolveBackendAssetSource } from "@/utils/api";

type Tab = "stock" | "web" | "ai" | "uploads" | "keys";

const STOCK_LABELS: Record<ImageSource, string> = {
  pexels: "Pexels",
  pixabay: "Pixabay",
  unsplash: "Unsplash",
};
const MAX_FILE_SIZE = 5 * 1024 * 1024;

function nearestAspect(size: { width: number; height: number } | null | undefined, aspects: string[]) {
  if (!size || !size.width || !size.height) return "16:9";
  const target = size.width / size.height;
  let best = aspects[0] ?? "16:9";
  let bestDiff = Infinity;
  for (const aspect of aspects) {
    const [w, h] = aspect.split(":").map(Number);
    const diff = Math.abs(Math.log(w / h / target));
    if (diff < bestDiff) {
      bestDiff = diff;
      best = aspect;
    }
  }
  return best;
}

function orientationFor(size: { width: number; height: number } | null | undefined): Orientation {
  if (!size || !size.width || !size.height) return "any";
  const ratio = size.width / size.height;
  if (ratio > 1.15) return "landscape";
  if (ratio < 0.87) return "portrait";
  return "square";
}

export function AsfImagePickerModal({
  currentImage,
  initialPrompt,
  targetSize,
  open,
  onClose,
  onSelect,
}: {
  currentImage?: string | null;
  initialPrompt?: string | null;
  targetSize?: { width: number; height: number } | null;
  open: boolean;
  onClose: () => void;
  onSelect: (url: string, prompt?: string) => void;
}) {
  const [tab, setTab] = useState<Tab>("stock");
  const [query, setQuery] = useState(initialPrompt || "");
  const [orientation, setOrientation] = useState<Orientation>("any");
  const [keys, setKeys] = useState<KeysResponse | null>(null);
  const [activeSources, setActiveSources] = useState<ImageSource[]>(["pexels", "pixabay", "unsplash"]);
  const [results, setResults] = useState<PlusImage[]>([]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const [aiOptions, setAiOptions] = useState<AiOptions | null>(null);
  const [aiStyle, setAiStyle] = useState("photo");
  const [aiAspect, setAiAspect] = useState("16:9");
  const [aiModel, setAiModel] = useState<"quality" | "fast">("fast");
  const [aiCount, setAiCount] = useState(2);
  const [aiNoText, setAiNoText] = useState(true);
  const [aiImages, setAiImages] = useState<PlusImage[]>([]);

  const [uploads, setUploads] = useState<PlusImage[]>([]);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const currentSource = resolveBackendAssetSource(currentImage || "");

  const loadKeys = useCallback(async () => {
    try {
      const status = await ImagesPlusApi.getKeys();
      setKeys(status);
      setActiveSources((previous) => {
        const configured = (Object.keys(status) as ImageSource[]).filter((s) => status[s].configured);
        return configured.length ? configured : previous;
      });
      return status;
    } catch {
      setKeys(null);
      return null;
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    setTab("stock");
    setQuery(initialPrompt || "");
    setResults([]);
    setErrors({});
    setMessage(null);
    setAiImages([]);
    setOrientation(orientationFor(targetSize));
    void loadKeys().then((status) => {
      if (status && !Object.values(status).some((s) => s.configured)) setTab("keys");
    });
    ImagesPlusApi.aiOptions()
      .then((options) => {
        setAiOptions(options);
        setAiAspect(nearestAspect(targetSize, options.aspects));
      })
      .catch(() => setAiOptions(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const configuredSources = useMemo(
    () => (keys ? (Object.keys(keys) as ImageSource[]).filter((s) => keys[s].configured) : []),
    [keys],
  );

  const runSearch = async () => {
    const text = query.trim();
    if (!text) return;
    setBusy(true);
    setMessage(null);
    setResults([]);
    setErrors({});
    try {
      if (tab === "stock") {
        const sources = activeSources.filter((s) => configuredSources.includes(s));
        if (!sources.length) {
          setMessage("Add at least one key in the Keys tab first.");
          return;
        }
        const response = await ImagesPlusApi.searchStock({ query: text, sources, orientation });
        setResults(response.results);
        setErrors(response.errors);
        if (!response.results.length) setMessage("No images found. Try different words.");
      } else if (tab === "web") {
        const response = await ImagesPlusApi.searchWeb({ query: text, orientation });
        setResults(response.results);
        if (!response.results.length) setMessage("No openly licensed images found. Try different words.");
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Search failed");
    } finally {
      setBusy(false);
    }
  };

  const runGenerate = async () => {
    const text = query.trim();
    if (!text) return;
    setBusy(true);
    setMessage(null);
    try {
      const response = await ImagesPlusApi.generate({
        prompt: text,
        style: aiStyle,
        aspect: aiAspect,
        model: aiModel,
        count: aiCount,
        no_text: aiNoText,
      });
      setAiImages((previous) => [...response.results.map((r) => ({ ...r, thumb: r.url })), ...previous]);
      if (response.failed) notify.warning("Some images failed", `${response.failed} of ${aiCount} could not be made.`);
    } catch (error) {
      const text2 = error instanceof Error ? error.message : "Image generation failed";
      setMessage(text2);
      notify.error("Image generation failed", text2);
    } finally {
      setBusy(false);
    }
  };

  const loadUploads = useCallback(async () => {
    try {
      const assets = await ImagesApi.getUploadedImages();
      setUploads(
        assets.flatMap((asset) => {
          const url = resolveBackendAssetSource(asset);
          return url ? [{ id: asset.id, source: "upload", thumb: url, url }] : [];
        }),
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load uploads");
    }
  }, []);

  useEffect(() => {
    if (open && tab === "uploads") void loadUploads();
  }, [open, tab, loadUploads]);

  const upload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!file.type.startsWith("image/") || file.size > MAX_FILE_SIZE) {
      setMessage("Choose an image file smaller than 5 MB.");
      return;
    }
    setBusy(true);
    try {
      await ImagesApi.uploadImage(file);
      await loadUploads();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  };

  const choose = async (image: PlusImage) => {
    setPicking(image.id);
    setMessage(null);
    try {
      if (image.source === "ai" || image.source === "upload") {
        onSelect(image.url, query.trim() || undefined);
      } else {
        const imported = await ImagesPlusApi.importImage(image, query.trim() || undefined);
        onSelect(imported.url, query.trim() || undefined);
      }
      onClose();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not add this image");
    } finally {
      setPicking(null);
    }
  };

  const grid = tab === "ai" ? aiImages : tab === "uploads" ? uploads : results;
  const showSearchBar = tab === "stock" || tab === "web" || tab === "ai";

  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-[10050] bg-black/80" />
        <DialogPrimitive.Content
          data-template-v2-floating-toolbar="true"
          data-inline-edit-ignore="true"
          style={{ translate: "none" }}
          className="fixed left-1/2 top-1/2 z-[10051] h-[calc(100dvh-24px)] w-[calc(100vw-24px)] -translate-x-1/2 -translate-y-1/2 font-syne text-[#191919] outline-none sm:h-[min(78vh,720px)] sm:min-h-[520px] sm:w-[min(calc(100vw-160px),1040px)]"
          onPointerDown={(event) => event.stopPropagation()}
        >
          <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-[20px] bg-[#F9FAFB] shadow-[0_28px_80px_rgba(0,0,0,0.34)]">
            <header className="flex flex-none items-center justify-between border-b border-[#EDEEEF] bg-white px-6 py-4">
              <div>
                <DialogPrimitive.Title className="text-[18px] font-normal">Change Image</DialogPrimitive.Title>
                <DialogPrimitive.Description className="mt-0.5 text-[13px] text-[#808080]">
                  Search stock photos, openly licensed web images, or create one with AI.
                </DialogPrimitive.Description>
              </div>
            </header>

            <div className="flex min-h-0 flex-1 flex-col sm:flex-row">
              <nav className="flex flex-none gap-1 border-b border-[#EDEEEF] bg-white p-3 sm:w-[200px] sm:flex-col sm:border-b-0 sm:border-r sm:p-4">
                <TabButton active={tab === "stock"} icon={<ImageIcon className="size-4" />} label="Stock photos" onClick={() => { setTab("stock"); setMessage(null); }} />
                <TabButton active={tab === "web"} icon={<Globe2 className="size-4" />} label="Web (open licence)" onClick={() => { setTab("web"); setResults([]); setMessage(null); }} />
                <TabButton active={tab === "ai"} icon={<Sparkles className="size-4" />} label="AI image" onClick={() => { setTab("ai"); setMessage(null); }} />
                <TabButton active={tab === "uploads"} icon={<Upload className="size-4" />} label="My uploads" onClick={() => { setTab("uploads"); setMessage(null); }} />
                <TabButton active={tab === "keys"} icon={<KeyRound className="size-4" />} label="Keys" onClick={() => { setTab("keys"); setMessage(null); void loadKeys(); }} />
              </nav>

              <div className="relative flex min-h-0 min-w-0 flex-1 flex-col bg-white px-4 py-4">
                {showSearchBar ? (
                  <div className="space-y-2">
                    <div className="flex gap-2">
                      <div className="relative flex-1">
                        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[#808080]" aria-hidden />
                        <input
                          value={query}
                          onChange={(event) => setQuery(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") void (tab === "ai" ? runGenerate() : runSearch());
                          }}
                          placeholder={tab === "ai" ? "Describe the image you want" : "Search images, e.g. healthy breakfast"}
                          className="h-10 w-full rounded-full border border-[#D7DAE3] pl-9 pr-3 text-sm outline-none focus:border-[#7C51F8]"
                        />
                      </div>
                      <button
                        type="button"
                        disabled={busy || !query.trim()}
                        onClick={() => void (tab === "ai" ? runGenerate() : runSearch())}
                        className="flex h-10 min-w-[96px] items-center justify-center rounded-full bg-[#7C51F8] px-4 text-sm text-white hover:bg-[#6d46e6] disabled:opacity-50"
                      >
                        {busy ? <Loader2 className="size-4 animate-spin" /> : tab === "ai" ? "Generate" : "Search"}
                      </button>
                    </div>

                    {tab !== "ai" ? (
                      <div className="flex flex-wrap items-center gap-2 text-xs">
                        <Segmented
                          value={orientation}
                          onChange={(value) => setOrientation(value as Orientation)}
                          options={[["any", "Any"], ["landscape", "Wide"], ["portrait", "Tall"], ["square", "Square"]]}
                        />
                        {tab === "stock"
                          ? (Object.keys(STOCK_LABELS) as ImageSource[]).map((source) => {
                              const configured = configuredSources.includes(source);
                              const on = activeSources.includes(source) && configured;
                              return (
                                <button
                                  key={source}
                                  type="button"
                                  disabled={!configured}
                                  title={configured ? undefined : "Add this key in the Keys tab"}
                                  onClick={() =>
                                    setActiveSources((previous) =>
                                      previous.includes(source) ? previous.filter((s) => s !== source) : [...previous, source],
                                    )
                                  }
                                  className={cn(
                                    "rounded-full border px-3 py-1",
                                    on ? "border-[#7C51F8] bg-[#F4F1FF] text-[#5B3BD0]" : "border-[#D7DAE3] text-[#6B7280]",
                                    !configured && "opacity-50",
                                  )}
                                >
                                  {STOCK_LABELS[source]}
                                </button>
                              );
                            })
                          : null}
                      </div>
                    ) : (
                      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
                        <label className="flex items-center gap-1.5">
                          Model
                          <select value={aiModel} onChange={(event) => setAiModel(event.target.value as "quality" | "fast")} className="h-8 rounded-[8px] border border-[#D7DAE3] px-2">
                            {(aiOptions?.models ?? [{ key: "fast", label: "Fast" }, { key: "quality", label: "Best quality" }]).map((m) => (
                              <option key={m.key} value={m.key}>{m.label}</option>
                            ))}
                          </select>
                        </label>
                        <label className="flex items-center gap-1.5">
                          Style
                          <select value={aiStyle} onChange={(event) => setAiStyle(event.target.value)} className="h-8 rounded-[8px] border border-[#D7DAE3] px-2">
                            {(aiOptions?.styles ?? [{ key: "photo", label: "Photorealistic" }]).map((s) => (
                              <option key={s.key} value={s.key}>{s.label}</option>
                            ))}
                          </select>
                        </label>
                        <label className="flex items-center gap-1.5">
                          Format
                          <select value={aiAspect} onChange={(event) => setAiAspect(event.target.value)} className="h-8 rounded-[8px] border border-[#D7DAE3] px-2">
                            {(aiOptions?.aspects ?? ["16:9", "4:3", "1:1", "3:4", "9:16"]).map((a) => (
                              <option key={a} value={a}>{a}</option>
                            ))}
                          </select>
                        </label>
                        <label className="flex items-center gap-1.5">
                          Variants
                          <select value={aiCount} onChange={(event) => setAiCount(Number(event.target.value))} className="h-8 rounded-[8px] border border-[#D7DAE3] px-2">
                            {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
                          </select>
                        </label>
                        <label className="flex items-center gap-1.5">
                          <input type="checkbox" checked={aiNoText} onChange={(event) => setAiNoText(event.target.checked)} />
                          No text in image
                        </label>
                      </div>
                    )}
                  </div>
                ) : null}

                {tab === "uploads" ? (
                  <div className="mb-2 flex items-center justify-between">
                    <p className="text-xs text-[#6B7280]">Images you uploaded earlier.</p>
                    <button type="button" onClick={() => fileRef.current?.click()} disabled={busy} className="flex h-8 items-center gap-1.5 rounded-full border border-[#D7DAE3] px-3 text-xs hover:bg-[#F7F8FB]">
                      {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Upload className="size-3.5" />} Upload
                    </button>
                    <input ref={fileRef} type="file" accept="image/*" className="sr-only" onChange={upload} />
                  </div>
                ) : null}

                {message ? (
                  <div className="mt-3 rounded-[10px] border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">{message}</div>
                ) : null}
                {Object.entries(errors).map(([source, text]) => (
                  <div key={source} className="mt-2 rounded-[10px] border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                    {STOCK_LABELS[source as ImageSource] ?? source}: {text}
                  </div>
                ))}

                <div className="mt-3 min-h-0 flex-1 overflow-y-auto pr-1 [scrollbar-width:thin]">
                  {tab === "keys" ? (
                    <div className="max-w-xl">
                      <ImageSourcesKeys compact />
                    </div>
                  ) : busy && !grid.length ? (
                    <p className="flex items-center gap-2 py-10 text-sm text-[#6B7280]">
                      <Loader2 className="size-4 animate-spin" /> {tab === "ai" ? "Creating images, this can take up to a minute…" : "Searching…"}
                    </p>
                  ) : grid.length ? (
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                      {grid.map((image) => (
                        <button
                          key={image.id}
                          type="button"
                          disabled={picking !== null}
                          onClick={() => void choose(image)}
                          className={cn(
                            "group relative aspect-[4/3] overflow-hidden rounded-[12px] border bg-[#F3F4F6] text-left",
                            currentSource && image.url === currentSource ? "border-[#7C51F8]" : "border-[#EDEEEF] hover:border-[#7C51F8]",
                          )}
                        >
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={image.thumb || image.url} alt={image.alt || ""} loading="lazy" className="h-full w-full object-cover" />
                          {image.credit ? (
                            <span className="absolute inset-x-0 bottom-0 truncate bg-gradient-to-t from-black/70 to-transparent px-2 pb-1 pt-4 text-[10px] text-white">
                              {image.source === "ai" ? "AI-generated" : `${image.credit.author || "Unknown"} · ${image.credit.license || image.source}`}
                            </span>
                          ) : null}
                          {picking === image.id ? (
                            <span className="absolute inset-0 flex items-center justify-center bg-white/70">
                              <Loader2 className="size-5 animate-spin" />
                            </span>
                          ) : null}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <p className="py-10 text-sm text-[#6B7280]">
                      {tab === "stock"
                        ? "Search Pexels, Pixabay and Unsplash together."
                        : tab === "web"
                          ? "Search openly licensed images from Wikimedia Commons, Flickr CC and museums. Credit is saved with each image."
                          : tab === "ai"
                            ? "Describe an image, pick a style and format, then Generate. Medical diagrams: check the result carefully."
                            : "No uploads yet."}
                    </p>
                  )}
                </div>
              </div>
            </div>
          </div>

          <DialogPrimitive.Close
            aria-label="Close image picker"
            className="absolute right-3 top-3 flex size-11 items-center justify-center rounded-full bg-white text-[#191919] shadow-sm transition hover:bg-[#F6F6F9] sm:-right-[68px] sm:top-0 sm:size-[52px]"
          >
            <X className="size-5" strokeWidth={1.5} aria-hidden />
          </DialogPrimitive.Close>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

function TabButton({ active, icon, label, onClick }: { active: boolean; icon: React.ReactNode; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "flex h-10 items-center gap-2 whitespace-nowrap rounded-[10px] px-3 text-sm transition",
        active ? "bg-[#F4F1FF] text-[#5B3BD0]" : "text-[#191919] hover:bg-[#F6F6F9]",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

function Segmented({ value, onChange, options }: { value: string; onChange: (value: string) => void; options: [string, string][] }) {
  return (
    <div className="inline-flex overflow-hidden rounded-full border border-[#D7DAE3]">
      {options.map(([key, label]) => (
        <button
          key={key}
          type="button"
          onClick={() => onChange(key)}
          className={cn("px-3 py-1", value === key ? "bg-[#F4F1FF] text-[#5B3BD0]" : "text-[#6B7280] hover:bg-[#F7F8FB]")}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
