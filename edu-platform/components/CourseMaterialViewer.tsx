"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { FileText, Hash, Loader2, AlertCircle, Download } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import MaterialPdfViewer from "@/components/MaterialPdfViewer";
import { isOfficeMaterialFileType } from "@/lib/material-office";
import { captureScrollViewportToPngFile, EDU_CHAT_ADD_ATTACHMENT_EVENT } from "@/lib/captureElementToPngFile";

type MaterialDetail = {
  id: string;
  filename: string;
  file_type: string;
  status: string;
  preview_pdf_status: "NA" | "PENDING" | "READY" | "FAILED";
  indexed_chunk_count: number;
  created_at: string;
  status_message: string | null;
};

type Props = {
  /** Reserved for future scoped URLs; optional. */
  courseId?: string;
  materialId: string | null;
  chunkId?: string;
  sourceLabel?: string;
};

export default function CourseMaterialViewer({
  materialId,
  chunkId,
  sourceLabel,
}: Props) {
  const [material, setMaterial] = useState<MaterialDetail | null>(null);
  const [chunkError, setChunkError] = useState<string | null>(null);
  const [textBody, setTextBody] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [captureBusy, setCaptureBusy] = useState(false);
  const [pdfViewportReady, setPdfViewportReady] = useState(false);
  const textScrollRef = useRef<HTMLDivElement>(null);
  const pdfScrollRef = useRef<HTMLDivElement>(null);

  const loadMaterial = useCallback(async () => {
    if (!materialId) {
      setMaterial(null);
      setTextBody(null);
      setChunkError(null);
      return;
    }
    setLoading(true);
    try {
      const mRes = await fetch(`/api/v1/materials/${materialId}`, {
        credentials: "include",
      });
      if (mRes.ok) {
        setMaterial((await mRes.json()) as MaterialDetail);
      } else {
        setMaterial(null);
      }
    } finally {
      setLoading(false);
    }
  }, [materialId]);

  useEffect(() => {
    void loadMaterial();
  }, [loadMaterial]);

  const office = material ? isOfficeMaterialFileType(material.file_type) : false;
  const pollPreview =
    !!material &&
    office &&
    material.preview_pdf_status === "PENDING";

  useEffect(() => {
    if (!pollPreview) return;
    const t = setInterval(() => void loadMaterial(), 2500);
    return () => clearInterval(t);
  }, [pollPreview, loadMaterial]);

  useEffect(() => {
    if (!materialId || !material) {
      setTextBody(null);
      return;
    }
    const ft = material.file_type.toLowerCase();
    if (ft !== "md" && ft !== "txt") {
      setTextBody(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      const res = await fetch(`/api/v1/materials/${materialId}/content`, {
        credentials: "include",
      });
      if (!res.ok || cancelled) return;
      const t = await res.text();
      if (!cancelled) setTextBody(t);
    })();
    return () => {
      cancelled = true;
    };
  }, [materialId, material?.file_type, material?.id]);

  useEffect(() => {
    setPdfViewportReady(false);
  }, [materialId]);

  useEffect(() => {
    if (!materialId || !chunkId) {
      setChunkError(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      const cRes = await fetch(
        `/api/v1/materials/${materialId}/chunks/${chunkId}`,
        { credentials: "include" },
      );
      if (cancelled) return;
      if (cRes.status === 501) {
        setChunkError("引用片段暂不支持从服务端拉取全文块，请查看下方资料预览。");
        return;
      }
      if (!cRes.ok) {
        setChunkError("无法加载引用片段");
        return;
      }
      setChunkError(null);
    })();
    return () => {
      cancelled = true;
    };
  }, [materialId, chunkId]);

  if (!materialId) {
    return (
      <div className="flex flex-col items-center justify-center h-full min-h-[120px] py-8 text-muted-foreground gap-2 px-3 text-center">
        <FileText size={26} />
        <p className="text-xs">选择资料即可预览</p>
      </div>
    );
  }

  if (loading && !material) {
    return (
      <div className="p-3 space-y-2">
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-3 w-1/2" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (!material) {
    return (
      <div className="p-3 text-xs text-destructive">无法加载该资料</div>
    );
  }

  const ft = material.file_type.toLowerCase();
  const showPdfIframe =
    ft === "pdf" ||
    (office && material.preview_pdf_status === "READY");
  const previewFailed =
    office && material.preview_pdf_status === "FAILED";
  const previewPending =
    office && material.preview_pdf_status === "PENDING";
  const previewPendingText =
    previewPending && material.status === "READY"
      ? "索引已完成，正在同步 PDF 预览状态…"
      : "正在生成 PDF 预览…";

  const textPreviewReady =
    (ft === "md" || ft === "txt") && textBody !== null;

  const screenshotTargetReady =
    (showPdfIframe && pdfViewportReady) ||
    textPreviewReady ||
    previewFailed;

  const screenshotDisabled =
    captureBusy ||
    loading ||
    previewPending ||
    !screenshotTargetReady;

  const safeScreenshotBase = material.filename
    .replace(/[/\\:*?"<>|]/g, "_")
    .slice(0, 80);

  const handleScreenshotToChat = async () => {
    const el = showPdfIframe
      ? pdfScrollRef.current
      : textScrollRef.current;
    if (!el || screenshotDisabled) return;
    setCaptureBusy(true);
    try {
      const stamp = new Date().toISOString().replace(/[:.]/g, "-");
      const file = await captureScrollViewportToPngFile(
        el,
        `资料截图-${safeScreenshotBase}-${stamp}.png`,
      );
      window.dispatchEvent(
        new CustomEvent(EDU_CHAT_ADD_ATTACHMENT_EVENT, {
          detail: { file },
        }),
      );
    } catch (e) {
      alert(e instanceof Error ? e.message : "截屏失败");
    } finally {
      setCaptureBusy(false);
    }
  };

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden">
      <div className="shrink-0 px-3 py-2 border-b border-border space-y-1">
        <div className="flex items-start gap-2">
          <FileText size={14} className="text-muted-foreground mt-0.5 shrink-0" />
          <div className="min-w-0">
            <p className="text-xs font-semibold leading-tight truncate">
              {material.filename}
            </p>
            <p className="text-[10px] text-muted-foreground uppercase font-mono">
              {material.file_type}
            </p>
          </div>
        </div>
        {sourceLabel && (
          <p className="text-[10px] text-muted-foreground line-clamp-2">
            <span className="font-medium text-foreground">引用：</span>
            {sourceLabel}
          </p>
        )}
        <div className="flex gap-2 items-stretch">
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="h-7 w-7 shrink-0"
            disabled={screenshotDisabled}
            title="截屏当前预览并添加到问答附件"
            onClick={() => void handleScreenshotToChat()}
            aria-label="截屏当前预览并添加到问答附件"
          >
            {captureBusy ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden
              >
                <path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z" />
                <circle cx="12" cy="13" r="3" />
              </svg>
            )}
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-[11px] flex-1 min-w-0"
            asChild
          >
            <a
              href={`/api/v1/materials/${materialId}/content?variant=original`}
              download={material.filename}
            >
              <Download size={12} className="mr-1 shrink-0" />
              下载原文件
            </a>
          </Button>
        </div>
      </div>

      <div
        ref={showPdfIframe ? undefined : textScrollRef}
        className={
          showPdfIframe
            ? "flex-1 min-h-0 flex flex-col overflow-hidden"
            : "flex-1 min-h-0 overflow-auto"
        }
      >
        {chunkId && chunkError && (
          <div className="mx-3 mt-2 rounded-lg border border-dashed border-border bg-muted/30 px-2 py-1.5 text-[10px] text-muted-foreground">
            {chunkError}
          </div>
        )}

        {previewPending && (
          <div className="flex items-center gap-2 m-3 text-xs text-muted-foreground">
            <Loader2 size={14} className="animate-spin" />
            {previewPendingText}
          </div>
        )}

        {previewFailed && (
          <div className="m-3 flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-2 text-[11px] text-destructive">
            <AlertCircle size={14} className="shrink-0 mt-0.5" />
            <span>预览转换失败，请下载原文件查看。</span>
          </div>
        )}

        {showPdfIframe && (
          <MaterialPdfViewer
            materialId={materialId}
            downloadHref={`/api/v1/materials/${materialId}/content?variant=original`}
            downloadName={material.filename}
            scrollCaptureRef={pdfScrollRef}
            onViewportCaptureReady={setPdfViewportReady}
          />
        )}

        {(ft === "md" || ft === "txt") && textBody !== null && (
          <div className="p-3 prose prose-sm dark:prose-invert max-w-none">
            {ft === "md" ? (
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{textBody}</ReactMarkdown>
            ) : (
              <pre className="whitespace-pre-wrap text-xs font-mono bg-muted/30 p-3 rounded-lg">
                {textBody}
              </pre>
            )}
          </div>
        )}

        {(ft === "md" || ft === "txt") && textBody === null && !loading && (
          <div className="p-3 flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 size={14} className="animate-spin" />
            加载文本…
          </div>
        )}

        {chunkId && !chunkError && (
          <div className="px-3 py-2 text-[10px] text-muted-foreground flex items-center gap-1">
            <Hash size={10} />
            <span className="font-mono truncate">{chunkId}</span>
          </div>
        )}
      </div>
    </div>
  );
}
