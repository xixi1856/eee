"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { FileText, Loader2, Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { isOfficeMaterialFileType } from "@/lib/material-office";
import { cn } from "@/lib/utils";

export type MaterialRow = {
  id: string;
  filename: string;
  file_type: string;
  lesson_id: string | null;
  status: string;
  preview_pdf_status: "NA" | "PENDING" | "READY" | "FAILED";
  indexed_chunk_count: number;
  created_at: string;
  status_message: string | null;
};

type Props = {
  courseId: string;
  activeMaterialId: string | null;
  onPickMaterial: (id: string) => void;
};

function PreviewHint({ m }: { m: MaterialRow }) {
  if (!isOfficeMaterialFileType(m.file_type)) return null;
  if (m.preview_pdf_status === "PENDING") {
    return (
      <span className="text-[9px] text-muted-foreground shrink-0">预览生成中</span>
    );
  }
  if (m.preview_pdf_status === "FAILED") {
    return (
      <span className="text-[9px] text-destructive shrink-0">预览失败</span>
    );
  }
  return null;
}

export default function CourseMaterialList({
  courseId,
  activeMaterialId,
  onPickMaterial,
}: Props) {
  const [materials, setMaterials] = useState<MaterialRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`/api/v1/courses/${courseId}/materials`, {
        credentials: "include",
      });
      if (!res.ok) {
        setMaterials([]);
        return;
      }
      const data = (await res.json()) as { materials: MaterialRow[] };
      setMaterials(data.materials ?? []);
    } finally {
      setLoading(false);
    }
  }, [courseId]);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return materials;
    return materials.filter(
      (m) =>
        m.filename.toLowerCase().includes(s) ||
        m.file_type.toLowerCase().includes(s),
    );
  }, [materials, q]);

  return (
    <div className="flex flex-col h-full min-h-0 bg-card/50">
      <div className="shrink-0 p-2 border-b border-border space-y-2">
        <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wide px-1">
          课程资料
        </p>
        <div className="relative">
          <Search
            size={14}
            className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜索文件名…"
            className="h-8 pl-8 text-xs"
          />
        </div>
      </div>

      <ScrollArea className="flex-1 min-h-0">
        <div className="p-1">
          {loading && (
            <div className="flex items-center justify-center gap-2 py-6 text-xs text-muted-foreground">
              <Loader2 size={14} className="animate-spin" />
              加载中…
            </div>
          )}
          {!loading && filtered.length === 0 && (
            <p className="text-xs text-muted-foreground text-center py-6 px-2">
              暂无资料
            </p>
          )}
          {filtered.map((m) => (
            <button
              key={m.id}
              type="button"
              onClick={() => onPickMaterial(m.id)}
              className={cn(
                "w-full flex items-start gap-2 rounded-lg px-2 py-2 text-left transition-colors",
                activeMaterialId === m.id
                  ? "bg-primary/12 text-foreground"
                  : "hover:bg-muted/60 text-foreground",
              )}
            >
              <FileText size={14} className="shrink-0 mt-0.5 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <p className="text-xs font-medium truncate">{m.filename}</p>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className="text-[10px] text-muted-foreground font-mono uppercase">
                    {m.file_type}
                  </span>
                  <PreviewHint m={m} />
                </div>
              </div>
            </button>
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}
