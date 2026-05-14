"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { FileText, Hash, Loader2, AlertCircle, Download, ChevronLeft, ChevronRight } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
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

const VIDEO_EXTENSIONS = new Set(["mp4", "webm", "mov", "mkv", "avi", "m4v", "wmv"]);
const AUDIO_EXTENSIONS = new Set(["mp3", "wav", "m4a", "flac", "ogg", "opus"]);

function isVideoType(ft: string) { return VIDEO_EXTENSIONS.has(ft.toLowerCase()); }
function isAudioType(ft: string) { return AUDIO_EXTENSIONS.has(ft.toLowerCase()); }

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

  // PDF canvas renderer state
  const [pdfPageNum, setPdfPageNum] = useState(1);
  const [pdfTotalPages, setPdfTotalPages] = useState(0);
  const [pdfRenderBusy, setPdfRenderBusy] = useState(false);
  const pdfCanvasRef = useRef<HTMLCanvasElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const pdfDocRef = useRef<any>(null);

  const textScrollRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

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
    setPdfPageNum(1);
    setPdfTotalPages(0);
    pdfDocRef.current = null;
  }, [materialId]);

  // Render PDF page onto canvas using pdfjs-dist
  const renderPdfPage = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    async (doc: any, pageNum: number) => {
      const canvas = pdfCanvasRef.current;
      if (!canvas) return;
      setPdfRenderBusy(true);
      try {
        const page = await doc.getPage(pageNum);
        const viewport = page.getViewport({ scale: 1.5 });
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        await page.render({ canvasContext: ctx, viewport }).promise;
      } finally {
        setPdfRenderBusy(false);
      }
    },
    [],
  );

  // Load PDF document when material is a native PDF and canvas is ready
  useEffect(() => {
    if (!materialId || !material) return;
    const ft = material.file_type.toLowerCase();
    if (ft !== "pdf") return;
    let cancelled = false;
    void (async () => {
      try {
        // Dynamic import to avoid SSR issues
        const pdfjsLib = await import("pdfjs-dist");
        pdfjsLib.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
        const loadingTask = pdfjsLib.getDocument({
          url: `/api/v1/materials/${materialId}/content`,
          withCredentials: true,
        });
        const doc = await loadingTask.promise;
        if (cancelled) { doc.destroy(); return; }
        pdfDocRef.current = doc;
        setPdfTotalPages(doc.numPages);
        setPdfPageNum(1);
        await renderPdfPage(doc, 1);
        setPdfViewportReady(true);
      } catch {
        if (!cancelled) setPdfViewportReady(false);
      }
    })();
    return () => { cancelled = true; };
  }, [materialId, material?.file_type, material?.id, renderPdfPage]);

  // Re-render when page number changes
  useEffect(() => {
    if (!pdfDocRef.current || pdfPageNum < 1) return;
    void renderPdfPage(pdfDocRef.current, pdfPageNum);
  }, [pdfPageNum, renderPdfPage]);

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
  const showPdfCanvas = ft === "pdf";
  const showOfficePdfIframe =
    office && material.preview_pdf_status === "READY";
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

  const isVideo = isVideoType(ft);
  const isAudio = isAudioType(ft);

  // Screenshot availability per type
  const canScreenshot =
    !captureBusy &&
    !loading &&
    !previewPending &&
    (
      textPreviewReady ||
      (showPdfCanvas && pdfViewportReady) ||
      isVideo ||
      previewFailed
    ) &&
    !isAudio &&
    !showOfficePdfIframe;

  const safeScreenshotBase = material.filename
    .replace(/[/\\:*?"<>|]/g, "_")
    .slice(0, 80);

  const handleScreenshotToChat = async () => {
    if (!canScreenshot) return;
    setCaptureBusy(true);
    try {
      const stamp = new Date().toISOString().replace(/[:.]/g, "-");
      let file: File;

      if (showPdfCanvas && pdfCanvasRef.current) {
        // PDF: capture the rendered canvas directly
        const canvas = pdfCanvasRef.current;
        const blob = await new Promise<Blob>((resolve, reject) => {
          canvas.toBlob((b) => b ? resolve(b) : reject(new Error("canvas.toBlob failed")), "image/png");
        });
        file = new File([blob], `资料截图-${safeScreenshotBase}-${stamp}.png`, { type: "image/png" });
      } else if (isVideo && videoRef.current) {
        // Video: capture current frame to canvas
        const video = videoRef.current;
        const tmpCanvas = document.createElement("canvas");
        tmpCanvas.width = video.videoWidth || 640;
        tmpCanvas.height = video.videoHeight || 360;
        const ctx = tmpCanvas.getContext("2d");
        if (!ctx) throw new Error("无法创建 canvas");
        ctx.drawImage(video, 0, 0, tmpCanvas.width, tmpCanvas.height);
        const blob = await new Promise<Blob>((resolve, reject) => {
          tmpCanvas.toBlob((b) => b ? resolve(b) : reject(new Error("canvas.toBlob failed")), "image/png");
        });
        file = new File([blob], `资料截图-${safeScreenshotBase}-${stamp}.png`, { type: "image/png" });
      } else {
        // Text/markdown
        const el = textScrollRef.current;
        if (!el) throw new Error("截屏目标不存在");
        file = await captureScrollViewportToPngFile(
          el,
          `资料截图-${safeScreenshotBase}-${stamp}.png`,
        );
      }

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
            disabled={!canScreenshot}
            title={isAudio ? "音频无法截屏" : "截屏当前预览并添加到问答附件"}
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
        ref={showPdfCanvas || showOfficePdfIframe || isVideo || isAudio ? undefined : textScrollRef}
        className={
          showPdfCanvas || showOfficePdfIframe
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

        {/* Office → converted PDF iframe */}
        {showOfficePdfIframe && (
          <div className="flex-1 min-h-0 flex flex-col relative overflow-hidden">
            {!pdfViewportReady && (
              <div className="absolute inset-0 flex items-center justify-center bg-background/80 z-10 pointer-events-none">
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 size={14} className="animate-spin" />
                  正在加载 PDF…
                </div>
              </div>
            )}
            <iframe
              src={`/api/v1/materials/${materialId}/content`}
              className="flex-1 w-full border-0"
              style={{ minHeight: 0 }}
              title={material.filename}
              onLoad={() => setPdfViewportReady(true)}
            />
          </div>
        )}

        {/* Native PDF → pdfjs-dist canvas */}
        {showPdfCanvas && (
          <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
            {!pdfViewportReady && (
              <div className="flex items-center gap-2 m-3 text-xs text-muted-foreground">
                <Loader2 size={14} className="animate-spin" />
                正在加载 PDF…
              </div>
            )}
            {pdfViewportReady && pdfTotalPages > 1 && (
              <div className="shrink-0 flex items-center justify-center gap-2 px-3 py-1 border-b border-border">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6"
                  disabled={pdfPageNum <= 1 || pdfRenderBusy}
                  onClick={() => setPdfPageNum((p) => Math.max(1, p - 1))}
                  aria-label="上一页"
                >
                  <ChevronLeft size={14} />
                </Button>
                <span className="text-[11px] text-muted-foreground tabular-nums">
                  {pdfPageNum} / {pdfTotalPages}
                </span>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6"
                  disabled={pdfPageNum >= pdfTotalPages || pdfRenderBusy}
                  onClick={() => setPdfPageNum((p) => Math.min(pdfTotalPages, p + 1))}
                  aria-label="下一页"
                >
                  <ChevronRight size={14} />
                </Button>
              </div>
            )}
            <div className="flex-1 min-h-0 overflow-auto flex justify-center p-2">
              {pdfRenderBusy && (
                <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                  <Loader2 size={14} className="animate-spin text-muted-foreground" />
                </div>
              )}
              <canvas
                ref={pdfCanvasRef}
                className="max-w-full h-auto shadow-sm rounded"
                style={{ display: pdfViewportReady ? "block" : "none" }}
              />
            </div>
          </div>
        )}

        {/* Video player */}
        {isVideo && (
          <div className="flex-1 min-h-0 flex flex-col items-center justify-center p-3 gap-2">
            {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
            <video
              ref={videoRef}
              src={`/api/v1/materials/${materialId}/content`}
              controls
              crossOrigin="anonymous"
              className="max-w-full max-h-full rounded shadow-sm"
              style={{ maxHeight: "calc(100% - 8px)" }}
              preload="metadata"
            />
          </div>
        )}

        {/* Audio player */}
        {isAudio && (
          <div className="flex items-center justify-center p-4">
            {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
            <audio
              src={`/api/v1/materials/${materialId}/content`}
              controls
              className="w-full"
              preload="metadata"
            />
          </div>
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
