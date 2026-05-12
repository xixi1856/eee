"use client";

import { useState } from "react";
import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { formatApiErrorFromResponse } from "@/lib/http/format-api-error";
import { cn } from "@/lib/utils";

export type RetryMaterialIndexPopoverProps = {
  courseId: string;
  materialId: string;
  triggerClassName?: string;
  iconSize?: number;
  onSuccess?: () => void | Promise<void>;
  onError?: (message: string) => void;
};

export function RetryMaterialIndexPopover({
  courseId,
  materialId,
  triggerClassName,
  iconSize = 13,
  onSuccess,
  onError,
}: RetryMaterialIndexPopoverProps) {
  const [open, setOpen] = useState(false);
  const [textOnly, setTextOnly] = useState(true);
  const [skipKg, setSkipKg] = useState(true);
  const [retrying, setRetrying] = useState(false);

  async function confirmRetry() {
    setRetrying(true);
    try {
      const res = await fetch(
        `/api/v1/courses/${courseId}/materials/${materialId}/retry-index`,
        {
          method: "POST",
          credentials: "include",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ text_only: textOnly, skip_kg: skipKg }),
        },
      );
      if (!res.ok) {
        const detail = formatApiErrorFromResponse(res.status, await res.text());
        onError?.(detail);
        return;
      }
      setOpen(false);
      await onSuccess?.();
    } finally {
      setRetrying(false);
    }
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setTextOnly(true);
          setSkipKg(true);
        }
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          title="重试索引（使用已解析的本地缓存）"
          disabled={retrying}
          className={cn(
            "text-muted-foreground hover:bg-muted hover:text-foreground transition-colors disabled:pointer-events-none disabled:opacity-60",
            triggerClassName,
          )}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
          }}
        >
          <RotateCcw size={iconSize} className={retrying ? "animate-spin" : ""} />
        </button>
      </PopoverTrigger>
      <PopoverContent
        className="w-80 space-y-3 p-3"
        align="end"
        side="left"
        collisionPadding={8}
        onClick={(e) => e.stopPropagation()}
      >
        <p className="text-xs font-medium text-foreground">重试索引选项</p>
        <label className="flex cursor-pointer items-start gap-2 rounded-md border border-border px-2 py-1.5 text-[11px] text-muted-foreground">
          <input
            type="checkbox"
            checked={textOnly}
            onChange={(e) => setTextOnly(e.target.checked)}
            className="mt-0.5 h-3 w-3 shrink-0 accent-primary"
          />
          <span>重试索引默认仅文本</span>
        </label>
        <label className="flex cursor-pointer items-start gap-2 rounded-md border border-border px-2 py-1.5 text-[11px] text-muted-foreground">
          <input
            type="checkbox"
            checked={skipKg}
            onChange={(e) => setSkipKg(e.target.checked)}
            className="mt-0.5 h-3 w-3 shrink-0 accent-primary"
          />
          <span>重试关闭实体/关系提取（多模态走文本化向量，无图谱）</span>
        </label>
        <p className="text-[10px] leading-snug text-muted-foreground">
          取消勾选则重试走完整 RAG-Anything 入库（含图谱）。与上传页的「关 KG /
          仅文本」语义一致。
        </p>
        <div className="flex justify-end gap-2 pt-1">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 text-xs"
            disabled={retrying}
            onClick={() => setOpen(false)}
          >
            取消
          </Button>
          <Button
            type="button"
            size="sm"
            className="h-8 text-xs"
            disabled={retrying}
            onClick={() => void confirmRetry()}
          >
            {retrying ? "重试中…" : "确认重试"}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
