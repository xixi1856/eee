"use client";

import { useEffect, useState } from "react";
import { FileText, Hash, Loader2 } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";

type Props = {
  courseId: string;
  materialId?: string;
  chunkId?: string;
  sourceLabel?: string;
};

type ChunkDetail = {
  chunk_id: string;
  content: string;
  metadata?: Record<string, unknown>;
};

type MaterialDetail = {
  id: string;
  filename: string;
  file_type: string;
  status: string;
};

export default function MaterialPreview({ courseId, materialId, chunkId, sourceLabel }: Props) {
  const [material, setMaterial] = useState<MaterialDetail | null>(null);
  const [chunk, setChunk] = useState<ChunkDetail | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!materialId) { setMaterial(null); setChunk(null); return; }
    setLoading(true);
    void (async () => {
      const [mRes, cRes] = await Promise.all([
        fetch(`/api/v1/materials/${materialId}`, { credentials: "include" }),
        chunkId
          ? fetch(`/api/v1/materials/${materialId}/chunks/${chunkId}`, { credentials: "include" })
          : Promise.resolve(null),
      ]);
      if (mRes.ok) setMaterial((await mRes.json()) as MaterialDetail);
      if (cRes?.ok) setChunk((await cRes.json()) as ChunkDetail);
      else setChunk(null);
      setLoading(false);
    })();
  }, [materialId, chunkId]);

  if (!materialId) {
    return (
      <div className="flex flex-col items-center justify-center h-full py-16 text-muted-foreground gap-2">
        <FileText size={28} />
        <p className="text-sm">点击引用标签预览资料原文</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="p-4 space-y-3">
        <Skeleton className="h-5 w-3/4" />
        <Skeleton className="h-3 w-1/3" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4">
      {/* Material info */}
      {material && (
        <div className="flex items-start gap-2">
          <FileText size={14} className="text-muted-foreground mt-0.5 shrink-0" />
          <div>
            <p className="text-sm font-semibold text-foreground leading-tight">{material.filename}</p>
            <p className="text-[11px] text-muted-foreground uppercase font-mono mt-0.5">{material.file_type}</p>
          </div>
        </div>
      )}

      {sourceLabel && (
        <p className="text-xs text-muted-foreground">
          <span className="font-medium text-foreground">引用来源：</span>{sourceLabel}
        </p>
      )}

      {/* Chunk content */}
      {chunk ? (
        <div className="space-y-2">
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Hash size={11} />
            <span className="font-mono">{chunk.chunk_id}</span>
          </div>
          <div className="rounded-xl border border-primary/20 bg-primary/5 p-4">
            <p className="text-sm leading-relaxed text-foreground whitespace-pre-wrap">{chunk.content}</p>
          </div>
        </div>
      ) : chunkId ? (
        <div className="rounded-xl border border-dashed border-border p-4 text-sm text-muted-foreground text-center">
          未找到对应文本块
        </div>
      ) : (
        <div className="rounded-xl border border-border p-4 text-sm text-muted-foreground">
          <p className="font-medium text-foreground mb-1">资料已加载</p>
          <p>该引用未包含具体文本块位置，请查看资料详情。</p>
        </div>
      )}
    </div>
  );
}
