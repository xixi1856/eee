"use client";

import { useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { Loader2, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";

type Props = {
  materialId: string;
  downloadHref: string;
  downloadName: string;
};

/**
 * Renders PDF via pdf.js + fetch (avoids iframe showing JSON error bodies).
 */
export default function MaterialPdfViewer({
  materialId,
  downloadHref,
  downloadName,
}: Props) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const docRef = useRef<PDFDocumentProxy | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let cancelled = false;
    docRef.current = null;
    container.innerHTML = "";
    setLoading(true);
    setError(null);

    void (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";

        let res: Response | null = null;
        let retryMsg = "预览 PDF 尚未生成，正在自动重试…";
        for (let attempt = 1; attempt <= 6; attempt++) {
          res = await fetch(`/api/v1/materials/${materialId}/content`, {
            credentials: "include",
          });
          if (res.ok) {
            break;
          }
          const ct = res.headers.get("content-type") ?? "";
          const j = ct.includes("application/json")
            ? ((await res.json().catch(() => null)) as {
                error?: { message?: string; code?: string };
              } | null)
            : null;
          if (j?.error?.code !== "PREVIEW_NOT_READY") {
            break;
          }
          retryMsg = j?.error?.message || retryMsg;
          if (attempt < 6) {
            if (!cancelled) setError(retryMsg);
            await new Promise((r) => setTimeout(r, 700 * attempt));
            continue;
          }
        }

        if (!res || !res.ok) {
          let msg = `无法加载 PDF（HTTP ${res?.status ?? 0}）`;
          const ct = res?.headers.get("content-type") ?? "";
          if (ct.includes("application/json")) {
            const j = (await res?.json().catch(() => null)) as {
              error?: { message?: string; code?: string };
            } | null;
            if (j?.error?.message) msg = j.error.message;
            if (j?.error?.code === "PREVIEW_NOT_READY") {
              msg = "预览 PDF 正在生成/修复中，请稍后重试。";
            }
            if (j?.error?.code === "NOT_FOUND") {
              msg =
                "内联预览使用的 PDF 在存储中缺失（例如仅有旧版预览键或对象被清理），原文件仍可正常下载。请使用下方「下载原文件」。";
            }
          }
          if (!cancelled) setError(msg);
          return;
        }

        const buf = await res.arrayBuffer();
        const task = pdfjs.getDocument({ data: buf });
        const pdf = await task.promise;
        if (cancelled) {
          await pdf.destroy().catch(() => {});
          return;
        }
        docRef.current = pdf;

        const scale = 1.2;
        for (let i = 1; i <= pdf.numPages; i++) {
          if (cancelled) break;
          const page = await pdf.getPage(i);
          const viewport = page.getViewport({ scale });
          const canvas = document.createElement("canvas");
          const ctx = canvas.getContext("2d");
          if (!ctx) continue;
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          canvas.className =
            "block w-full max-w-full mb-4 rounded-md border border-border bg-card shadow-sm";
          canvas.style.height = "auto";
          container.appendChild(canvas);
          await page
            .render({
              canvasContext: ctx,
              viewport,
            })
            .promise;
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "PDF 解析失败");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
      container.innerHTML = "";
      void docRef.current?.destroy().catch(() => {});
      docRef.current = null;
    };
  }, [materialId]);

  return (
    <div className="flex flex-col min-h-0 flex-1">
      {loading && (
        <div className="flex items-center gap-2 p-4 text-xs text-muted-foreground">
          <Loader2 size={14} className="animate-spin shrink-0" />
          正在加载 PDF…
        </div>
      )}
      {error && (
        <div className="m-3 flex flex-col gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
          <div className="flex items-start gap-2">
            <AlertCircle size={14} className="shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
          <Button variant="outline" size="sm" className="h-8 w-fit text-destructive" asChild>
            <a href={downloadHref} download={downloadName}>
              下载原文件
            </a>
          </Button>
        </div>
      )}
      <div
        ref={containerRef}
        className="flex-1 min-h-0 overflow-auto px-2 py-2 bg-muted/15"
      />
    </div>
  );
}
