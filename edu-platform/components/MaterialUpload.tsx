"use client";


import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Upload, CheckCircle2, AlertCircle, Loader2, X, ChevronDown, Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatApiErrorFromResponse } from "@/lib/http/format-api-error";
import {
  MATERIAL_UPLOAD_ACCEPT,
  MATERIAL_UPLOAD_ALLOWED_EXT_SET,
  materialUploadAllowedLabel,
} from "@/lib/material-upload-allowed";

type Lesson = { id: string; title: string };

type Props = {
  courseId: string;
  lessons?: Lesson[];
  onUploaded?: () => void;
};

function parseExtension(filename: string): string {
  const i = filename.lastIndexOf(".");
  if (i < 0) return "";
  return filename.slice(i + 1).toLowerCase();
}

export default function MaterialUpload({ courseId, lessons = [], onUploaded }: Props) {
  const [open, setOpen] = useState(false);

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          <Upload size={14} />
          上传资料
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-2xl bg-card border border-border shadow-xl p-6 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95"
          onInteractOutside={(e) => e.preventDefault()}
        >
          <UploadForm
            courseId={courseId}
            lessons={lessons}
            onUploaded={() => {
              onUploaded?.();
              setOpen(false);
            }}
            onClose={() => setOpen(false)}
          />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function LessonPicker({
  lessons,
  selectedLessonId,
  onSelect,
  disabled,
}: {
  lessons: Lesson[];
  selectedLessonId: string | null;
  onSelect: (id: string | null) => void;
  disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const selectedLesson = lessons.find((l) => l.id === selectedLessonId);

  // Close when clicking outside
  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-foreground">归属课时</p>
      <div ref={containerRef} className="relative">
        <button
          type="button"
          disabled={disabled}
          onClick={() => setOpen((v) => !v)}
          className={cn(
            "w-full flex items-center justify-between gap-2 rounded-xl border border-border bg-background px-3 py-2.5 text-sm transition-colors",
            "hover:border-primary/60 hover:bg-muted/20",
            open && "border-primary ring-1 ring-primary/30",
            disabled && "opacity-50 cursor-not-allowed",
          )}
        >
          <span className={cn("truncate text-left", !selectedLesson && "text-muted-foreground")}>
            {selectedLesson ? selectedLesson.title : "不分配课时（未分类资料）"}
          </span>
          <ChevronDown
            size={14}
            className={cn(
              "shrink-0 text-muted-foreground transition-transform duration-150",
              open && "rotate-180",
            )}
          />
        </button>

        {open && (
          <div className="absolute z-10 mt-1 w-full rounded-xl border border-border bg-popover shadow-lg p-1 max-h-52 overflow-y-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            <button
              type="button"
              onClick={() => { onSelect(null); setOpen(false); }}
              className={cn(
                "w-full flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-left transition-colors hover:bg-muted/60",
                selectedLessonId === null && "bg-primary/10 text-primary font-medium",
              )}
            >
              <Check size={13} className={cn("shrink-0", selectedLessonId !== null && "invisible")} />
              <span>不分配课时</span>
            </button>
            {lessons.map((lesson, idx) => (
              <button
                key={lesson.id}
                type="button"
                onClick={() => { onSelect(lesson.id); setOpen(false); }}
                className={cn(
                  "w-full flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-left transition-colors hover:bg-muted/60",
                  selectedLessonId === lesson.id && "bg-primary/10 text-primary font-medium",
                )}
              >
                <Check size={13} className={cn("shrink-0", selectedLessonId !== lesson.id && "invisible")} />
                <span className="text-xs text-muted-foreground shrink-0 w-5 text-right font-mono">{idx + 1}.</span>
                <span className="truncate">{lesson.title}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function UploadForm({
  courseId,
  lessons,
  onUploaded,
  onClose,
}: {
  courseId: string;
  lessons: Lesson[];
  onUploaded: () => void;
  onClose: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const xhrRef = useRef<XMLHttpRequest | null>(null);
  const [selectedLessonId, setSelectedLessonId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [textOnly, setTextOnly] = useState(true);
  const [skipKg, setSkipKg] = useState(false);
  const [pct, setPct] = useState(0);
  const [status, setStatus] = useState<"idle" | "success" | "error">("idle");
  const [msg, setMsg] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  function cancelUpload() {
    xhrRef.current?.abort();
  }

  async function handleFiles(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    setStatus("idle");
    setMsg(null);
    setPct(0);
    const list = Array.from(files);
    let done = 0;
    try {
      for (const file of list) {
        const ext = parseExtension(file.name);
        if (!ext || !MATERIAL_UPLOAD_ALLOWED_EXT_SET.has(ext)) {
          throw new Error(
            `不支持的文件格式${ext ? `（.${ext}）` : "（缺少扩展名）"}。当前支持：${materialUploadAllowedLabel()}`,
          );
        }
        const fd = new FormData();
        fd.set("file", file);
        if (selectedLessonId) fd.set("lesson_id", selectedLessonId);
        fd.set("text_only", textOnly ? "true" : "false");
        fd.set("skip_kg", skipKg ? "true" : "false");
        const xhr = new XMLHttpRequest();
        await new Promise<void>((resolve, reject) => {
          xhrRef.current = xhr;
          xhr.open("POST", `/api/v1/courses/${courseId}/materials`);
          xhr.withCredentials = true;
          xhr.upload.onprogress = (ev) => {
            if (ev.lengthComputable) setPct(Math.round((ev.loaded / ev.total) * 100));
          };
          xhr.onload = () => {
            if (xhr.status >= 200 && xhr.status < 300) {
              done++;
              resolve();
            } else {
              const detail = formatApiErrorFromResponse(xhr.status, xhr.responseText || "");
              reject(new Error(detail));
            }
          };
          xhr.onerror = () => reject(new Error("网络错误，请检查连接后重试"));
          xhr.onabort = () => {
            const err = new Error("已取消");
            (err as Error & { isAbort: boolean }).isAbort = true;
            reject(err);
          };
          xhr.send(fd);
        });
      }
      setStatus("success");
      setMsg(`已上传 ${done} 个文件，等待后台处理…`);
      onUploaded();
    } catch (e) {
      if (e instanceof Error && (e as Error & { isAbort?: boolean }).isAbort) {
        setStatus("idle");
        setMsg("已取消");
        setTimeout(() => setMsg(null), 2000);
      } else {
        setStatus("error");
        setMsg(e instanceof Error ? e.message : "上传失败");
      }
    } finally {
      setBusy(false);
      setPct(0);
      xhrRef.current = null;
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div>
          <Dialog.Title className="text-base font-semibold text-foreground">上传课程资料</Dialog.Title>
          <Dialog.Description className="text-xs text-muted-foreground mt-0.5">
            选择归属课时并上传文件
          </Dialog.Description>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted/60 hover:text-foreground transition-colors"
        >
          <X size={15} />
        </button>
      </div>

      {/* Lesson selector */}
      {lessons.length > 0 && (
        <LessonPicker
          lessons={lessons}
          selectedLessonId={selectedLessonId}
          onSelect={setSelectedLessonId}
          disabled={busy}
        />
      )}

      {/* Options */}
      <div className="space-y-2">
        <label className="flex items-start gap-2.5 rounded-xl border border-border px-3 py-2.5 text-xs cursor-pointer hover:bg-muted/20 transition-colors">
          <input
            type="checkbox"
            checked={textOnly}
            disabled={busy}
            onChange={(e) => setTextOnly(e.target.checked)}
            className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-primary"
          />
          <div>
            <p className="font-medium text-foreground">仅文本索引</p>
            <p className="text-[11px] text-muted-foreground mt-0.5">开启后跳过图片、表格、公式等多模态块</p>
          </div>
        </label>
        <label className="flex items-start gap-2.5 rounded-xl border border-border px-3 py-2.5 text-xs cursor-pointer hover:bg-muted/20 transition-colors">
          <input
            type="checkbox"
            checked={skipKg}
            disabled={busy}
            onChange={(e) => setSkipKg(e.target.checked)}
            className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-primary"
          />
          <div>
            <p className="font-medium text-foreground">关闭实体与关系提取</p>
            <p className="text-[11px] text-muted-foreground mt-0.5">不做知识图谱抽取，只写入向量索引</p>
          </div>
        </label>
      </div>

      {/* Drop zone */}
      <div
        role="button"
        tabIndex={busy ? -1 : 0}
        aria-disabled={busy}
        onClick={() => { if (!busy) inputRef.current?.click(); }}
        onKeyDown={(e) => { if (!busy && (e.key === "Enter" || e.key === " ")) inputRef.current?.click(); }}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void handleFiles(e.dataTransfer.files);
        }}
        className={cn(
          "relative w-full rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors cursor-pointer",
          dragging ? "border-primary bg-primary/8" : "border-border hover:border-primary/50 hover:bg-muted/30",
          busy && "pointer-events-none opacity-60",
        )}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={MATERIAL_UPLOAD_ACCEPT}
          className="hidden"
          onChange={(e) => void handleFiles(e.target.files)}
        />
        {busy ? (
          <div className="flex flex-col items-center gap-2">
            <Loader2 size={20} className="animate-spin text-primary" />
            <p className="text-sm font-medium text-foreground">上传中… {pct}%</p>
            <div className="w-40 h-1.5 rounded-full bg-muted overflow-hidden">
              <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${pct}%` }} />
            </div>
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); cancelUpload(); }}
              className="mt-1 flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted/60 hover:text-foreground transition-colors"
            >
              <X size={12} />
              取消上传
            </button>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2">
            <Upload size={20} className="text-muted-foreground" />
            <p className="text-sm font-medium text-foreground">拖放文件或点击选择</p>
            <p className="text-xs text-muted-foreground">
              支持 {materialUploadAllowedLabel().replace(/、/g, " · ")}
            </p>
          </div>
        )}
      </div>

      {/* Status message */}
      {msg && (
        <div
          className={cn(
            "flex items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium",
            status === "success"
              ? "bg-[oklch(0.92_0.08_145)] text-[oklch(0.35_0.10_145)]"
              : "bg-destructive/10 text-destructive",
          )}
        >
          {status === "success" ? <CheckCircle2 size={13} /> : <AlertCircle size={13} />}
          {msg}
        </div>
      )}
    </div>
  );
}

