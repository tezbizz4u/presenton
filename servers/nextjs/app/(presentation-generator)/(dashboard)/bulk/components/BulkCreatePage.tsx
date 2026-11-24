"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";

const API = "/api/v1/ppt/bulk";
const TEMPLATES = [
  "momentum", "civic", "dynamic", "editorial", "executive", "general", "horizon", "landmark",
  "modern", "mosaic", "nova", "pulse", "signal", "standard", "swift", "verdant",
];

type Row = { name: string; brief: string; slides: number };
type Quality = {
  slides?: number; notes_min?: number; notes_short?: number; images?: number;
  duplicate_images?: number; layout_repeat_max?: number; distinct_layouts?: number;
};
type Job = {
  idx: number; name: string; slides: number; status: string; seconds?: number; error?: string;
  edit_path?: string; quality?: Quality; cross_deck_duplicate_images?: number;
  cross_deck_duplicate_titles?: number;
};
type Batch = {
  id: string; total: number; done: number; failed: number; running: number;
  finished_at?: number | null; clinical_review: boolean; jobs: Job[];
};

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* keep default */ }
    throw new Error(detail);
  }
  return res.json();
}

const STATUS_STYLE: Record<string, string> = {
  queued: "bg-slate-100 text-slate-700",
  running: "bg-blue-100 text-blue-800",
  done: "bg-green-100 text-green-800",
  error: "bg-red-100 text-red-800",
  cancelled: "bg-amber-100 text-amber-800",
};

export default function BulkCreatePage() {
  const [text, setText] = useState("");
  const [courseName, setCourseName] = useState("");
  const [audience, setAudience] = useState("");
  const [template, setTemplate] = useState("momentum");
  const [defaultSlides, setDefaultSlides] = useState(25);
  const [verbosity, setVerbosity] = useState("standard");
  const [parallel, setParallel] = useState(3);
  const [exportAs, setExportAs] = useState("pptx");
  const [clinical, setClinical] = useState(true);
  const [rows, setRows] = useState<Row[]>([]);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = () => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
  };

  const poll = useCallback(async (id: string) => {
    try {
      const b = await call<Batch>(`/${id}`);
      setBatch(b);
      if (b.finished_at) stopPolling();
    } catch (e) {
      setError((e as Error).message);
      stopPolling();
    }
  }, []);

  useEffect(() => stopPolling, []);

  // Resume the last batch after a reload: jobs run on the server, the page only watches them.
  useEffect(() => {
    let id = "";
    try { id = localStorage.getItem("asf_bulk_batch") || ""; } catch { /* ignore */ }
    if (!id) return;
    (async () => {
      try {
        const b = await call<Batch>(`/${id}`);
        setBatch(b);
        if (!b.finished_at) timer.current = setInterval(() => poll(id), 5000);
      } catch { /* batch gone: show the empty form */ }
    })();
  }, [poll]);

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    setText(await file.text());
  };

  const parse = async () => {
    setError("");
    setBusy(true);
    try {
      const r = await call<{ jobs: Row[]; truncated: boolean }>("/parse", {
        method: "POST",
        body: JSON.stringify({ text, default_slides: defaultSlides }),
      });
      setRows(r.jobs);
      if (r.truncated) setError("Only the first 60 decks are used per batch.");
    } catch (e) {
      setRows([]);
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const start = async () => {
    setError("");
    setBusy(true);
    try {
      const b = await call<Batch>("/start", {
        method: "POST",
        body: JSON.stringify({
          course_name: courseName, audience, jobs: rows, template, verbosity, parallel,
          export_as: exportAs, clinical_review: clinical,
        }),
      });
      setBatch(b);
      try { localStorage.setItem("asf_bulk_batch", b.id); } catch { /* ignore */ }
      stopPolling();
      timer.current = setInterval(() => poll(b.id), 5000);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const retry = async () => {
    if (!batch) return;
    try {
      const b = await call<Batch>(`/${batch.id}/retry`, { method: "POST" });
      setBatch(b);
      stopPolling();
      timer.current = setInterval(() => poll(batch.id), 5000);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const cancel = async () => {
    if (!batch) return;
    try {
      setBatch(await call<Batch>(`/${batch.id}/cancel`, { method: "POST" }));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const totalSlides = rows.reduce((a, r) => a + (Number(r.slides) || 0), 0);
  const running = !!batch && !batch.finished_at;
  const field = "w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm";
  const label = "mb-1 block text-xs font-medium text-slate-700";

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Bulk Create</h1>
        <p className="text-sm text-slate-600">
          Many decks from one blueprint. Every deck uses the same generation pipeline as a single deck,
          so fonts, images, speaker notes and Sources follow the same rules. Jobs run on the server:
          you can close this page and come back.
        </p>
      </div>

      {error ? <div className="rounded-md bg-red-50 p-3 text-sm text-red-800">{error}</div> : null}

      {!batch ? (
        <>
          <section className="space-y-4 rounded-lg border border-slate-200 p-4">
            <h2 className="font-medium">1. Blueprint</h2>
            <p className="text-xs text-slate-600">
              Paste or upload. Each <code>## Heading</code> becomes one deck, or use lines like
              <code> name | slides | brief</code>, CSV (name, slides, brief) or JSON.
            </p>
            <textarea
              className={`${field} h-48 font-mono`}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={"## Module 1 - Diagnosis of PCOS\nRotterdam criteria, phenotypes, work-up. 25 slides\n\n## Module 2 - ..."}
            />
            <div className="flex flex-wrap items-center gap-3">
              <input type="file" accept=".md,.txt,.csv,.json" onChange={(e) => onFile(e.target.files?.[0])} />
              <button
                disabled={!text.trim() || busy}
                onClick={parse}
                className="rounded-md bg-[#5146E5] px-4 py-2 text-sm text-white disabled:opacity-50"
              >
                Find decks
              </button>
            </div>
          </section>

          <section className="grid grid-cols-2 gap-4 rounded-lg border border-slate-200 p-4 md:grid-cols-4">
            <div className="col-span-2">
              <label className={label}>Course name</label>
              <input className={field} value={courseName} onChange={(e) => setCourseName(e.target.value)} />
            </div>
            <div className="col-span-2">
              <label className={label}>Audience</label>
              <input className={field} value={audience} onChange={(e) => setAudience(e.target.value)} />
            </div>
            <div>
              <label className={label}>Template</label>
              <select className={field} value={template} onChange={(e) => setTemplate(e.target.value)}>
                {TEMPLATES.map((t) => <option key={t}>{t}</option>)}
              </select>
            </div>
            <div>
              <label className={label}>Default slides per deck</label>
              <input type="number" min={1} max={200} className={field} value={defaultSlides}
                onChange={(e) => setDefaultSlides(Number(e.target.value) || 25)} />
            </div>
            <div>
              <label className={label}>Detail</label>
              <select className={field} value={verbosity} onChange={(e) => setVerbosity(e.target.value)}>
                <option value="concise">Concise</option>
                <option value="standard">Standard</option>
                <option value="text-heavy">Text heavy</option>
              </select>
            </div>
            <div>
              <label className={label}>Decks in parallel (1-4)</label>
              <input type="number" min={1} max={4} className={field} value={parallel}
                onChange={(e) => setParallel(Math.min(4, Math.max(1, Number(e.target.value) || 3)))} />
            </div>
            <div>
              <label className={label}>Export</label>
              <select className={field} value={exportAs} onChange={(e) => setExportAs(e.target.value)}>
                <option value="pptx">PPTX</option>
                <option value="pdf">PDF</option>
              </select>
            </div>
            <label className="col-span-2 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={clinical} onChange={(e) => setClinical(e.target.checked)} />
              Mark decks as needing clinical review (medical content)
            </label>
          </section>

          {rows.length ? (
            <section className="space-y-3 rounded-lg border border-slate-200 p-4">
              <h2 className="font-medium">2. Decks found: {rows.length} ({totalSlides} slides)</h2>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-600">
                    <th className="p-1">Deck</th><th className="w-24 p-1">Slides</th><th className="w-16 p-1" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i} className="border-t border-slate-100">
                      <td className="p-1">
                        <input className={field} value={r.name}
                          onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
                      </td>
                      <td className="p-1">
                        <input type="number" min={1} max={200} className={field} value={r.slides}
                          onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, slides: Number(e.target.value) || 1 } : x)))} />
                      </td>
                      <td className="p-1">
                        <button className="text-xs text-red-700" onClick={() => setRows(rows.filter((_, j) => j !== i))}>Remove</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <button
                disabled={busy || !rows.length}
                onClick={start}
                className="rounded-md bg-[#5146E5] px-5 py-2 text-sm text-white disabled:opacity-50"
              >
                Create {rows.length} decks
              </button>
            </section>
          ) : null}
        </>
      ) : (
        <section className="space-y-4 rounded-lg border border-slate-200 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm">
              <b>{batch.done}</b> of {batch.total} done · {batch.running} running · {batch.failed} failed
              {batch.finished_at ? " · finished" : ""}
            </div>
            <div className="flex flex-wrap gap-2 text-sm">
              {running ? <button className="rounded-md border px-3 py-1" onClick={cancel}>Stop queued decks</button> : null}
              {batch.failed && !running ? <button className="rounded-md border px-3 py-1" onClick={retry}>Retry failed</button> : null}
              <a className="rounded-md border px-3 py-1" href={`${API}/${batch.id}/report.csv`}>Quality report (CSV)</a>
              <a className="rounded-md bg-[#5146E5] px-3 py-1 text-white" href={`${API}/${batch.id}/zip`}>Download ZIP</a>
              <button className="rounded-md border px-3 py-1" onClick={() => { stopPolling(); setBatch(null); try { localStorage.removeItem("asf_bulk_batch"); } catch { /* ignore */ } }}>New batch</button>
            </div>
          </div>
          {batch.clinical_review ? (
            <p className="rounded-md bg-amber-50 p-2 text-xs text-amber-900">
              Medical content: every deck, especially its Sources slide, needs ASF clinical review before use.
            </p>
          ) : null}
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-600">
                <th className="p-1">Deck</th><th className="p-1">Status</th><th className="p-1">Time</th>
                <th className="p-1">Slides</th><th className="p-1">Notes min</th><th className="p-1">Images</th>
                <th className="p-1">Dup. images</th><th className="p-1">Layouts</th><th className="p-1" />
              </tr>
            </thead>
            <tbody>
              {batch.jobs.map((j) => (
                <tr key={j.idx} className="border-t border-slate-100 align-top">
                  <td className="p-1">{j.name}{j.error ? <div className="text-xs text-red-700">{j.error}</div> : null}</td>
                  <td className="p-1"><span className={`rounded px-2 py-0.5 text-xs ${STATUS_STYLE[j.status] || ""}`}>{j.status}</span></td>
                  <td className="p-1">{j.seconds ? `${Math.round(j.seconds)} s` : ""}</td>
                  <td className="p-1">{j.quality?.slides ?? j.slides}</td>
                  <td className={`p-1 ${(j.quality?.notes_short ?? 0) > 0 ? "text-red-700" : ""}`}>{j.quality?.notes_min ?? ""}</td>
                  <td className="p-1">{j.quality?.images ?? ""}</td>
                  <td className={`p-1 ${((j.quality?.duplicate_images ?? 0) + (j.cross_deck_duplicate_images ?? 0)) > 0 ? "text-amber-700" : ""}`}>
                    {j.quality ? (j.quality.duplicate_images ?? 0) + (j.cross_deck_duplicate_images ?? 0) : ""}
                  </td>
                  <td className="p-1">{j.quality?.distinct_layouts ?? ""}</td>
                  <td className="p-1">{j.edit_path ? <a className="text-[#5146E5]" href={j.edit_path} target="_blank">Open</a> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
