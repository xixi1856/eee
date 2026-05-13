"use client";

import { useRef, useState } from "react";
import { Upload, CheckCircle2, AlertCircle, Loader2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatApiErrorFromResponse } from "@/lib/http/format-api-error";
import {
  MATERIAL_UPLOAD_ACCEPT,
  MATERIAL_UPLOAD_ALLOWED_EXT_SET,
  materialUploadAllowedLabel,
} from "@/lib/material-upload-allowed";

type Props = {
  courseId: string;
  lessonId?: string | null;
  onUploaded?: () => void;
};

function parseExtension(filename: string): string {
  const i = filename.lastIndexOf(".");
  if (i < 0) return "";
  return filename.slice(i + 1).toLowerCase();
}

export default function MaterialUpload({ courseId, lessonId, onUploaded }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const xhrRef = useRef<XMLHttpRequest | null>(null);
  const [busy, setBusy] = useState(false);
  const [textOnly, setTextOnly] = useState(true);
  const [skipKg, setSkipKg] = useState(true);
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
        if (lessonId) fd.set("lesson_id", lessonId);
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
      onUploaded?.();
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
    <div className="space-y-3">
      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">上传资料</p>

      <label className="flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-xs text-foreground">
        <input
          type="checkbox"
          checked={textOnly}
          disabled={busy}
          onChange={(e) => setTextOnly(e.target.checked)}
          className="h-3.5 w-3.5 accent-primary"
        />
        <span className="font-medium">仅文本索引（节省 token）</span>
      </label>
      <p className="text-[11px] text-muted-foreground px-1">
        开启后跳过 list、image、table、equation、chart、code 等多模态块。
      </p>

      <label className="flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-xs text-foreground">
        <input
          type="checkbox"
          checked={skipKg}
          disabled={busy}
          onChange={(e) => setSkipKg(e.target.checked)}
          className="h-3.5 w-3.5 accent-primary"
        />
        <span className="font-medium">关闭实体与关系提取（省 LLM，默认推荐）</span>
      </label>
      <p className="text-[11px] text-muted-foreground px-1">
        开启后不做知识图谱抽取，只写入向量。多模态块（图、表、公式等）会先转成可检索文本再嵌入；关闭「仅文本」时仍不会走 RAG-Anything 的图谱入库。若需要完整多模态图谱与实体关系，请取消勾选。
      </p>

      {/* Drop zone */}
      <div
        role="button"
        tabIndex={busy ? -1 : 0}
        aria-disabled={busy}
        onClick={() => { if (!busy) inputRef.current?.click(); }}
        onKeyDown={(e) => { if (!busy && (e.key === 'Enter' || e.key === ' ')) inputRef.current?.click(); }}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void handleFiles(e.dataTransfer.files);
        }}
        className={cn(
          "relative w-full rounded-xl border-2 border-dashed px-4 py-6 text-center transition-colors cursor-pointer",
          dragging ? "border-primary bg-primary/8" : "border-border hover:border-primary/50 hover:bg-muted/30",
          busy && "pointer-events-none opacity-60"
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

