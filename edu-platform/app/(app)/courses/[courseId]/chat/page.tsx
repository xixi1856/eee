"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ChevronLeft, X } from "lucide-react";
import ChatComponent from "@/components/ChatComponent";
import MaterialPreview from "@/components/MaterialPreview";

type PreviewState = {
  materialId?: string;
  chunkId?: string;
  sourceLabel?: string;
};

export default function CourseChatPage() {
  const params = useParams();
  const courseId = typeof params?.courseId === "string" ? params.courseId : null;
  const [preview, setPreview] = useState<PreviewState | null>(null);

  useEffect(() => {
    const h = (ev: Event) => {
      const ce = ev as CustomEvent<PreviewState>;
      if (ce.detail?.materialId) setPreview(ce.detail);
    };
    window.addEventListener("edu:open-material-preview", h as EventListener);
    return () => window.removeEventListener("edu:open-material-preview", h as EventListener);
  }, []);

  if (!courseId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        无效的课程链接
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border bg-background/80 backdrop-blur-sm px-4 py-3 shrink-0">
        <Link
          href={`/courses/${courseId}`}
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ChevronLeft size={15} />
          返回课程
        </Link>
        <h2 className="font-display text-sm font-semibold text-foreground">课程问答</h2>
        <div className="w-20" />
      </div>

      {/* Main area */}
      <div className="flex flex-1 overflow-hidden">
        {/* Chat */}
        <div
          className={`flex flex-col transition-all duration-200 ${preview?.materialId ? "w-[55%] border-r border-border" : "w-full"}`}
        >
          <ChatComponent courseId={courseId} />
        </div>

        {/* Material preview panel */}
        {preview?.materialId && (
          <div className="flex flex-col w-[45%] overflow-hidden bg-card">
            <div className="flex items-center justify-between border-b border-border px-4 py-2.5 shrink-0">
              <span className="text-xs font-medium text-muted-foreground truncate">
                {preview.sourceLabel ?? "资料预览"}
              </span>
              <button
                onClick={() => setPreview(null)}
                className="flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
              >
                <X size={13} />
              </button>
            </div>
            <div className="flex-1 overflow-auto">
              <MaterialPreview
                courseId={courseId}
                materialId={preview.materialId}
                chunkId={preview.chunkId}
                sourceLabel={preview.sourceLabel}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
