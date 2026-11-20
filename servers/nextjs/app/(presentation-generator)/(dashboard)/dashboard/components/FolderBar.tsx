"use client";

import React, { useMemo, useState } from "react";
import { Folder, FolderPlus, Inbox, Layers, Pencil, Trash } from "lucide-react";

import {
  FolderApi,
  type FolderResponse,
} from "@/app/(presentation-generator)/services/api/dashboard";
import { notify } from "@/components/ui/sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export type FolderSelection = "all" | "unfiled" | string;

export type FolderNode = FolderResponse & { depth: number; path: string };

/** Flatten folders into a depth-first list (children right after their parent). */
export const buildFolderTree = (folders: FolderResponse[]): FolderNode[] => {
  const byParent = new Map<string | null, FolderResponse[]>();
  const ids = new Set(folders.map((f) => f.id));
  for (const folder of folders) {
    const key = folder.parent_id && ids.has(folder.parent_id) ? folder.parent_id : null;
    byParent.set(key, [...(byParent.get(key) ?? []), folder]);
  }
  const result: FolderNode[] = [];
  const walk = (parent: string | null, depth: number, prefix: string) => {
    const children = (byParent.get(parent) ?? []).sort((a, b) =>
      a.name.localeCompare(b.name),
    );
    for (const child of children) {
      const path = prefix ? `${prefix} / ${child.name}` : child.name;
      result.push({ ...child, depth, path });
      walk(child.id, depth + 1, path);
    }
  };
  walk(null, 0, "");
  return result;
};

/** The folder id plus the ids of every folder nested inside it. */
export const folderAndDescendantIds = (
  folders: FolderResponse[],
  folderId: string,
): Set<string> => {
  const result = new Set<string>([folderId]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const folder of folders) {
      if (folder.parent_id && result.has(folder.parent_id) && !result.has(folder.id)) {
        result.add(folder.id);
        grew = true;
      }
    }
  }
  return result;
};

type Props = {
  folders: FolderResponse[];
  selected: FolderSelection;
  totalCount: number;
  unfiledCount: number;
  onSelect: (selection: FolderSelection) => void;
  onChanged: () => void | Promise<void>;
};

type DialogState =
  | { kind: "create" }
  | { kind: "rename"; folder: FolderNode }
  | { kind: "delete"; folder: FolderNode }
  | null;

const chipBase =
  "flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm font-syne transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#7A5AF8]";

export const FolderBar = ({
  folders,
  selected,
  totalCount,
  unfiledCount,
  onSelect,
  onChanged,
}: Props) => {
  const tree = useMemo(() => buildFolderTree(folders), [folders]);
  const [dialog, setDialog] = useState<DialogState>(null);
  const [name, setName] = useState("");
  const [parentId, setParentId] = useState<string>("");
  const [busy, setBusy] = useState(false);

  const selectedFolder = tree.find((f) => f.id === selected) ?? null;

  const openCreate = () => {
    setName("");
    // New folders are created inside the currently open folder, if any.
    setParentId(selectedFolder ? selectedFolder.id : "");
    setDialog({ kind: "create" });
  };

  const openRename = (folder: FolderNode) => {
    setName(folder.name);
    setDialog({ kind: "rename", folder });
  };

  const run = async (action: () => Promise<unknown>, success: string) => {
    if (busy) return;
    setBusy(true);
    try {
      await action();
      notify.success(success);
      setDialog(null);
      await onChanged();
    } catch (error) {
      notify.error(
        "Folder action failed",
        error instanceof Error ? error.message : "Something went wrong.",
      );
    } finally {
      setBusy(false);
    }
  };

  const submit = () => {
    const trimmed = name.trim();
    if (!trimmed || !dialog) return;
    if (dialog.kind === "create") {
      void run(() => FolderApi.create(trimmed, parentId || null), "Folder created");
    } else if (dialog.kind === "rename") {
      void run(
        () => FolderApi.update(dialog.folder.id, { name: trimmed }),
        "Folder renamed",
      );
    }
  };

  const chip = (active: boolean) =>
    `${chipBase} ${
      active
        ? "border-[#7A5AF8] bg-[#F4F0FF] text-[#191919]"
        : "border-[#EDEEEF] bg-white text-[#4B4B4B] hover:bg-[#F8F8FA]"
    }`;

  return (
    <div className="mb-4">
      <div className="flex items-center gap-2 overflow-x-auto pb-2">
        <button type="button" className={chip(selected === "all")} onClick={() => onSelect("all")}>
          <Layers className="h-4 w-4" /> All <span className="text-[#808080]">{totalCount}</span>
        </button>
        <button
          type="button"
          className={chip(selected === "unfiled")}
          onClick={() => onSelect("unfiled")}
        >
          <Inbox className="h-4 w-4" /> Unfiled <span className="text-[#808080]">{unfiledCount}</span>
        </button>
        {tree.map((folder) => (
          <button
            key={folder.id}
            type="button"
            title={folder.path}
            className={chip(selected === folder.id)}
            onClick={() => onSelect(folder.id)}
            style={{ marginLeft: folder.depth ? 8 : 0 }}
          >
            <Folder className="h-4 w-4" />
            {folder.depth > 0 ? `↳ ${folder.name}` : folder.name}
            <span className="text-[#808080]">{folder.presentation_count}</span>
          </button>
        ))}
        <button
          type="button"
          onClick={openCreate}
          className={`${chipBase} border-dashed border-[#B9B9C3] bg-white text-[#4B4B4B] hover:bg-[#F8F8FA]`}
        >
          <FolderPlus className="h-4 w-4" /> New folder
        </button>
      </div>

      {selectedFolder && (
        <div className="flex items-center gap-3 text-sm text-[#808080] font-syne">
          <span className="truncate">{selectedFolder.path}</span>
          <button
            type="button"
            onClick={() => openRename(selectedFolder)}
            className="flex items-center gap-1 hover:text-[#191919]"
          >
            <Pencil className="h-3.5 w-3.5" /> Rename
          </button>
          <button
            type="button"
            onClick={() => setDialog({ kind: "delete", folder: selectedFolder })}
            className="flex items-center gap-1 hover:text-[#191919]"
          >
            <Trash className="h-3.5 w-3.5" /> Delete
          </button>
        </div>
      )}

      <Dialog open={dialog !== null} onOpenChange={(open) => !open && setDialog(null)}>
        <DialogContent>
          {dialog?.kind === "delete" ? (
            <>
              <DialogHeader>
                <DialogTitle>Delete folder “{dialog.folder.name}”?</DialogTitle>
                <DialogDescription>
                  Presentations and sub-folders inside it are not deleted. They move up one
                  level.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <button
                  type="button"
                  className="rounded-md border border-[#EDEEEF] px-4 py-2 text-sm"
                  onClick={() => setDialog(null)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={busy}
                  className="rounded-md bg-[#191919] px-4 py-2 text-sm text-white disabled:opacity-60"
                  onClick={() =>
                    void run(async () => {
                      await FolderApi.remove(dialog.folder.id);
                      onSelect("all");
                    }, "Folder deleted")
                  }
                >
                  Delete folder
                </button>
              </DialogFooter>
            </>
          ) : (
            <>
              <DialogHeader>
                <DialogTitle>{dialog?.kind === "rename" ? "Rename folder" : "New folder"}</DialogTitle>
                <DialogDescription>
                  {dialog?.kind === "rename"
                    ? "Choose a new name for this folder."
                    : "Group related presentations, e.g. one folder per course module."}
                </DialogDescription>
              </DialogHeader>
              <input
                autoFocus
                value={name}
                maxLength={120}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && submit()}
                placeholder="Folder name"
                className="w-full rounded-md border border-[#EDEEEF] px-3 py-2 text-sm outline-none focus:border-[#7A5AF8]"
              />
              {dialog?.kind === "create" && (
                <select
                  value={parentId}
                  onChange={(e) => setParentId(e.target.value)}
                  className="w-full rounded-md border border-[#EDEEEF] px-3 py-2 text-sm"
                >
                  <option value="">Top level</option>
                  {tree.map((folder) => (
                    <option key={folder.id} value={folder.id}>
                      Inside: {folder.path}
                    </option>
                  ))}
                </select>
              )}
              <DialogFooter>
                <button
                  type="button"
                  className="rounded-md border border-[#EDEEEF] px-4 py-2 text-sm"
                  onClick={() => setDialog(null)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={busy || !name.trim()}
                  className="rounded-md bg-[#191919] px-4 py-2 text-sm text-white disabled:opacity-60"
                  onClick={submit}
                >
                  {dialog?.kind === "rename" ? "Save" : "Create"}
                </button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
};
