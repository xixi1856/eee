"use client";

import { useRef, useState } from "react";
import { Upload, CheckCircle2, AlertCircle, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

type Props = {
  courseId: string;
  lessonId?: string | null;
  onUploaded?: () => void;
};

const ACCEPT = ".pdf,.pptx,.docx,.md,.txt,.jpg,.jpeg,.png,.webp";

export default function MaterialUpload({ courseId, lessonId, onUploaded }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [pct, setPct] = useState(0);
  const [status, setStatus] = useState<"idle" | "success" | "error">("idle");
  const [msg, setMsg] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

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
        const fd = new FormData();
        fd.set("file", file);
        if (lessonId) fd.set("lesson_id", lessonId);
        const xhr = new XMLHttpRequest();
        await new Promise<void>((resolve, reject) => {
          xhr.open("POST", `/api/v1/courses/${courseId}/materials`);
          xhr.withCredentials = true;
          xhr.upload.onprogress = (ev) => {
            if (ev.lengthComputable) setPct(Math.round((ev.loaded / ev.total) * 100));
          };
          xhr.onload = () => {
            if (xhr.status >= 200 && xhr.status < 300) { done++; resolve(); }
            else reject(new Error(`上传失败 (${xhr.status})`));
          };
          xhr.onerror = () => reject(new Error("网络错误"));
          xhr.send(fd);
        });
      }
      setStatus("success");
      setMsg(`已上传 ${done} 个文件，等待后台处理…`);
      onUploaded?.();
    } catch (e) {
      setStatus("error");
      setMsg(e instanceof Error ? e.message : "上传失败");
    } finally {
      setBusy(false);
      setPct(0);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">上传资料</p>

      {/* Drop zone */}
      <button
        type="button"
        disabled={busy}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void handleFiles(e.dataTransfer.files);
        }}
        className={cn(
          "relative w-full rounded-xl border-2 border-dashed px-4 py-6 text-center transition-colors",
          dragging ? "border-primary bg-primary/8" : "border-border hover:border-primary/50 hover:bg-muted/30",
          busy && "pointer-events-none opacity-60"
        )}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT}
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
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2">
            <Upload size={20} className="text-muted-foreground" />
            <p className="text-sm font-medium text-foreground">拖放文件或点击选择</p>
            <p className="text-xs text-muted-foreground">支持 PDF · PPTX · DOCX · MD · TXT · 图片</p>
          </div>
        )}
      </button>

      {/* Status message */}
      {msg && (
        <div className={cn(
          "flex items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium",
          status === "success" ? "bg-[oklch(0.92_0.08_145)] text-[oklch(0.35_0.10_145)]" : "bg-destructive/10 text-destructive"
        )}>
          {status === "success" ? <CheckCircle2 size={13} /> : <AlertCircle size={13} />}
          {msg}
        </div>
      )}
    </div>
  );
}

