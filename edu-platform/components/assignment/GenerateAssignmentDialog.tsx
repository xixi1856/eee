"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Loader2, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

const schema = z.object({
  title: z.string().min(1, "请填写作业标题"),
  teacherRequest: z
    .string()
    .min(10, "请描述作业需求（至少 10 个字）"),
  deadline: z.string().optional(),
});

type FormValues = z.infer<typeof schema>;

interface GenerateAssignmentDialogProps {
  courseId: string;
  onClose: () => void;
  onCreated: (assignmentId: string) => void;
}

export function GenerateAssignmentDialog({
  courseId,
  onClose,
  onCreated,
}: GenerateAssignmentDialogProps) {
  const [submitting, setSubmitting] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  async function onSubmit(values: FormValues) {
    setSubmitting(true);
    setApiError(null);
    try {
      const res = await fetch(`/api/v1/courses/${courseId}/assignments`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(values),
      });
      const data = (await res.json()) as { assignment?: { id: string }; error?: { message: string } };
      if (!res.ok) {
        setApiError(data.error?.message ?? "创建失败，请稍后重试");
        return;
      }
      onCreated(data.assignment!.id);
    } catch {
      setApiError("网络错误，请检查连接后重试");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="relative w-full max-w-lg rounded-xl bg-card shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b px-6 py-4">
          <div className="flex items-center gap-2">
            <Sparkles size={18} className="text-primary" />
            <h2 className="text-base font-semibold">AI 生成作业</h2>
          </div>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
            aria-label="关闭"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <form onSubmit={(e) => void handleSubmit(onSubmit)(e)} className="px-6 py-5 space-y-4">
          {/* Title */}
          <div>
            <label className="text-sm font-medium mb-1 block">作业标题</label>
            <Input placeholder="例：第三章课后练习" {...register("title")} />
            {errors.title && (
              <p className="text-xs text-destructive mt-1">{errors.title.message}</p>
            )}
          </div>

          {/* Teacher Request */}
          <div>
            <label className="text-sm font-medium mb-1 block">作业需求描述</label>
            <textarea
              className={cn(
                "w-full rounded-md border border-input bg-background px-3 py-2 text-sm resize-none",
                "focus:outline-none focus:ring-2 focus:ring-ring min-h-[100px]",
              )}
              placeholder="例：围绕传输层 TCP/UDP，生成 10 道混合题（包含判断题、单选题、简答题），难度中等，考察实际应用场景"
              {...register("teacherRequest")}
            />
            {errors.teacherRequest && (
              <p className="text-xs text-destructive mt-1">{errors.teacherRequest.message}</p>
            )}
          </div>

          {/* Deadline */}
          <div>
            <label className="text-sm font-medium mb-1 block">截止时间（选填）</label>
            <Input type="datetime-local" {...register("deadline")} />
          </div>

          {/* API error */}
          {apiError && (
            <p className="rounded-md bg-destructive/10 text-destructive text-sm px-3 py-2">
              {apiError}
            </p>
          )}

          {/* Submit */}
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="outline" onClick={onClose} disabled={submitting}>
              取消
            </Button>
            <Button type="submit" disabled={submitting} className="gap-2">
              {submitting ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
              {submitting ? "提交中…" : "开始生成"}
            </Button>
          </div>
        </form>

        {/* Info banner */}
        <div className="rounded-b-xl bg-muted/40 border-t px-6 py-3 text-xs text-muted-foreground">
          生成过程约需 30–120 秒，期间可关闭此窗口，任务在后台运行。
        </div>
      </div>
    </div>
  );
}
