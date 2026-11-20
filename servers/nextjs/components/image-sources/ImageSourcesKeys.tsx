"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Loader2 } from "lucide-react";
import {
  ImagesPlusApi,
  type ImageSource,
  type KeysResponse,
} from "@/app/(presentation-generator)/services/api/images-plus";

const SOURCES: { key: ImageSource; label: string; help: string }[] = [
  { key: "pexels", label: "Pexels", help: "pexels.com/api" },
  { key: "pixabay", label: "Pixabay", help: "pixabay.com/api/docs" },
  { key: "unsplash", label: "Unsplash", help: "unsplash.com/developers (Access Key)" },
];

type Status = { kind: "ok" | "error"; text: string } | null;

/** Keys for the stock sources, kept together on the server (not in the browser). */
export function ImageSourcesKeys({ compact = false }: { compact?: boolean }) {
  const [saved, setSaved] = useState<KeysResponse | null>(null);
  const [drafts, setDrafts] = useState<Record<ImageSource, string>>({
    pexels: "",
    pixabay: "",
    unsplash: "",
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [status, setStatus] = useState<Record<ImageSource, Status>>({
    pexels: null,
    pixabay: null,
    unsplash: null,
  });

  const load = useCallback(async () => {
    try {
      setSaved(await ImagesPlusApi.getKeys());
    } catch (error) {
      setSaved(null);
      setStatus((s) => ({
        ...s,
        pexels: { kind: "error", text: error instanceof Error ? error.message : "Could not load keys" },
      }));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const setOne = (source: ImageSource, value: Status) =>
    setStatus((s) => ({ ...s, [source]: value }));

  const test = async (source: ImageSource) => {
    setBusy(`test-${source}`);
    setOne(source, null);
    try {
      const draft = drafts[source].trim();
      const result = await ImagesPlusApi.testKey(source, draft || undefined);
      setOne(source, { kind: "ok", text: `Key works (${result.found} sample images)` });
    } catch (error) {
      setOne(source, { kind: "error", text: error instanceof Error ? error.message : "Test failed" });
    } finally {
      setBusy(null);
    }
  };

  const save = async (source: ImageSource, remove = false) => {
    const value = remove ? "" : drafts[source].trim();
    if (!remove && !value) return;
    setBusy(`save-${source}`);
    setOne(source, null);
    try {
      setSaved(await ImagesPlusApi.saveKeys({ [source]: value }));
      setDrafts((d) => ({ ...d, [source]: "" }));
      setOne(source, { kind: "ok", text: remove ? "Key removed" : "Saved on the server" });
    } catch (error) {
      setOne(source, { kind: "error", text: error instanceof Error ? error.message : "Could not save" });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className={compact ? "space-y-3" : "space-y-4"}>
      {SOURCES.map(({ key, label, help }) => {
        const info = saved?.[key];
        const draft = drafts[key];
        const st = status[key];
        return (
          <div key={key} className="rounded-[12px] border border-[#EDEEEF] bg-white p-4">
            <div className="flex items-center justify-between">
              <p className="text-sm font-medium text-black">{label}</p>
              <span className="flex items-center gap-1 text-[11px] text-[#6B7280]">
                {info?.configured ? (
                  <>
                    <CheckCircle2 className="size-3.5 text-emerald-600" aria-hidden />
                    Saved {info.hint}
                  </>
                ) : (
                  <>Not set · {help}</>
                )}
              </span>
            </div>
            <div className="mt-2 flex gap-2">
              <input
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={draft}
                placeholder={info?.configured ? "Enter a new key to replace" : "Paste the key"}
                onChange={(event) => setDrafts((d) => ({ ...d, [key]: event.target.value }))}
                className="h-9 min-w-0 flex-1 rounded-[8px] border border-[#D7DAE3] px-3 text-xs outline-none focus:border-[#7C51F8]"
              />
              <button
                type="button"
                disabled={busy !== null || (!draft.trim() && !info?.configured)}
                onClick={() => void test(key)}
                className="h-9 rounded-[8px] border border-[#D7DAE3] bg-white px-3 text-xs hover:bg-[#F7F8FB] disabled:opacity-50"
              >
                {busy === `test-${key}` ? <Loader2 className="size-3.5 animate-spin" /> : "Test"}
              </button>
              <button
                type="button"
                disabled={busy !== null || !draft.trim()}
                onClick={() => void save(key)}
                className="h-9 rounded-[8px] bg-[#7C51F8] px-3 text-xs text-white hover:bg-[#6d46e6] disabled:opacity-50"
              >
                {busy === `save-${key}` ? <Loader2 className="size-3.5 animate-spin" /> : "Save"}
              </button>
              {info?.configured ? (
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void save(key, true)}
                  className="h-9 rounded-[8px] border border-[#F2C7C7] bg-white px-3 text-xs text-[#B42318] hover:bg-[#FEF3F2] disabled:opacity-50"
                >
                  Remove
                </button>
              ) : null}
            </div>
            {st ? (
              <p className={`mt-2 text-[11px] ${st.kind === "ok" ? "text-emerald-700" : "text-red-600"}`}>{st.text}</p>
            ) : null}
          </div>
        );
      })}
      <p className="text-[11px] leading-4 text-[#6B7280]">
        Keys stay on this server and are never shown in full. Test checks a key before you save it.
      </p>
    </div>
  );
}
