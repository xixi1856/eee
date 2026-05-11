"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  ChevronLeft,
  Sparkles,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Clock,
  BookOpen,
  FileQuestion,
  Star,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { GenerateAssignmentDialog } from "@/components/assignment/GenerateAssignmentDialog";
import { useNotify } from "@/hooks/useNotify";
import type { AssignmentSummaryDto } from "@/lib/dto/assignment.dto";
import { AssignmentStatus } from "@prisma/client";

const STATUS_LABELS: Record<string, string> = {
  GENERATING: "生成中",
  FAILED: "失败",
  DRAFT: "草稿",
  PUBLISHED: "已发布",
  ARCHIVED: "已归档",
};

const STATUS_CLASSES: Record<string, string> = {
  GENERATING: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300",
  FAILED: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300",
  DRAFT: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300",
  PUBLISHED: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300",
  ARCHIVED: "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold", STATUS_CLASSES[status])}>
      {status === "GENERATING" && <Loader2 size={10} className="animate-spin" />}
      {status === "FAILED" && <AlertCircle size={10} />}
      {status === "PUBLISHED" && <CheckCircle2 size={10} />}
      {status === "DRAFT" && <Clock size={10} />}
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

function AssignmentRow({ a, courseId }: { a: AssignmentSummaryDto; courseId: string }) {
  return (
    <Link
      href={`/courses/${courseId}/assignments/${a.id}`}
      className="flex items-center gap-4 rounded-lg border bg-card px-5 py-4 hover:bg-muted/20 transition-colors"
    >
      <FileQuestion size={20} className="shrink-0 text-muted-foreground" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold truncate">{a.title}</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          {a.questionCount > 0 ? `${a.questionCount} 题` : "暂无题目"}
          {a.qualityScore !== null && (
            <span className="ml-2 inline-flex items-center gap-0.5">
              <Star size={10} />
              {a.qualityScore} 分
            </span>
          )}
          {a.deadline && (
            <span className="ml-2">截止 {new Date(a.deadline).toLocaleDateString("zh-CN")}</span>
          )}
        </p>
        {a.errorMessage && (
          <p className="text-xs text-destructive mt-0.5 truncate">{a.errorMessage}</p>
        )}
      </div>
      <StatusBadge status={a.status} />
    </Link>
  );
}

export default function AssignmentsPage() {
  const { courseId } = useParams<{ courseId: string }>();
  const router = useRouter();
  const { notification, notify } = useNotify();

  const [loading, setLoading] = useState(true);
  const [assignments, setAssignments] = useState<AssignmentSummaryDto[]>([]);
  const [showDialog, setShowDialog] = useState(false);

  const load = useCallback(async () => {
    const res = await fetch(`/api/v1/courses/${courseId}/assignments`, { credentials: "include" });
    if (res.ok) {
      const d = (await res.json()) as { assignments: AssignmentSummaryDto[] };
      setAssignments(d.assignments);
    }
    setLoading(false);
  }, [courseId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll while any assignment is GENERATING
  useEffect(() => {
    const hasGenerating = assignments.some((a) => a.status === (AssignmentStatus.GENERATING as string));
    if (!hasGenerating) return;
    const id = setInterval(() => void load(), 5000);
    return () => clearInterval(id);
  }, [assignments, load]);

  function handleCreated(assignmentId: string) {
    setShowDialog(false);
    notify("success", "作业生成任务已提交，后台处理中…");
    void load();
    // Navigate to detail page after a short delay
    setTimeout(() => router.push(`/courses/${courseId}/assignments/${assignmentId}`), 800);
  }

  return (
    <div className="max-w-3xl mx-auto px-4 py-6 space-y-6">
      {/* Notification */}
      {notification && (
        <div
          className={cn(
            "fixed top-4 right-4 z-50 rounded-lg px-4 py-3 text-sm shadow-lg flex items-center gap-2",
            notification.type === "success"
              ? "bg-green-600 text-white"
              : "bg-destructive text-destructive-foreground",
          )}
        >
          {notification.type === "success" ? <CheckCircle2 size={15} /> : <AlertCircle size={15} />}
          {notification.msg}
        </div>
      )}

      {/* Header */}
      <div className="flex items-center gap-3">
        <Link
          href={`/courses/${courseId}`}
          className="text-muted-foreground hover:text-foreground"
        >
          <ChevronLeft size={20} />
        </Link>
        <div className="flex items-center gap-2">
          <BookOpen size={18} className="text-primary" />
          <h1 className="text-lg font-semibold">作业管理</h1>
        </div>
        <div className="flex-1" />
        <Button
          size="sm"
          className="gap-1.5"
          onClick={() => setShowDialog(true)}
        >
          <Sparkles size={14} />
          AI 生成作业
        </Button>
      </div>

      {/* List */}
      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-16 rounded-lg" />
          ))}
        </div>
      ) : assignments.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center text-muted-foreground gap-3">
          <FileQuestion size={40} className="opacity-30" />
          <p className="text-sm">暂无作业，点击右上角按钮生成第一份作业</p>
        </div>
      ) : (
        <div className="space-y-3">
          {assignments.map((a) => (
            <AssignmentRow key={a.id} a={a} courseId={courseId} />
          ))}
        </div>
      )}

      {/* Dialog */}
      {showDialog && (
        <GenerateAssignmentDialog
          courseId={courseId}
          onClose={() => setShowDialog(false)}
          onCreated={handleCreated}
        />
      )}
    </div>
  );
}
