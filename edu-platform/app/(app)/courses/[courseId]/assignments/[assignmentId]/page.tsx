"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  ChevronLeft,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Save,
  Send,
  Clock,
  Star,
  FileQuestion,
  Plus,
} from "lucide-react";
import {
  DndContext,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  verticalListSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { QuestionCard } from "@/components/assignment/QuestionCard";
import { AddQuestionDialog } from "@/components/assignment/AddQuestionDialog";
import { useNotify } from "@/hooks/useNotify";
import type { AssignmentDetailDto, CompleteQuestionBody, QuestionItem, RegenerateQuestionBody } from "@/lib/dto/assignment.dto";
import { AssignmentStatus } from "@prisma/client";

const STATUS_LABELS: Record<string, string> = {
  GENERATING: "生成中",
  FAILED: "失败",
  DRAFT: "草稿",
  PUBLISHED: "已发布",
  ARCHIVED: "已归档",
};

export default function AssignmentDetailPage() {
  const { courseId, assignmentId } = useParams<{ courseId: string; assignmentId: string }>();
  const router = useRouter();
  const { notification, notify } = useNotify();

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [regeneratingIds, setRegeneratingIds] = useState<Set<string>>(new Set());
  const [addDialogOpen, setAddDialogOpen] = useState(false);

  const [assignment, setAssignment] = useState<AssignmentDetailDto | null>(null);
  const [title, setTitle] = useState("");
  const [questions, setQuestions] = useState<QuestionItem[]>([]);

  const sensors = useSensors(useSensor(PointerSensor));

  const load = useCallback(async () => {
    const res = await fetch(
      `/api/v1/courses/${courseId}/assignments/${assignmentId}`,
      { credentials: "include" },
    );
    if (res.ok) {
      const d = (await res.json()) as { assignment: AssignmentDetailDto };
      setAssignment(d.assignment);
      setTitle(d.assignment.title);
      setQuestions((d.assignment.questions as QuestionItem[]) ?? []);
    }
    setLoading(false);
  }, [courseId, assignmentId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll while GENERATING
  useEffect(() => {
    if (assignment?.status !== AssignmentStatus.GENERATING) return;
    const id = setInterval(() => void load(), 5000);
    return () => clearInterval(id);
  }, [assignment?.status, load]);

  // ── Drag-and-drop ──────────────────────────────────────────────────────────
  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (over && active.id !== over.id) {
      setQuestions((prev) => {
        const oldIdx = prev.findIndex((q) => String(q.id) === String(active.id));
        const newIdx = prev.findIndex((q) => String(q.id) === String(over.id));
        return arrayMove(prev, oldIdx, newIdx);
      });
    }
  }

  // ── Question CRUD ──────────────────────────────────────────────────────────
  function handleUpdateQuestion(id: number, updates: Partial<QuestionItem>) {
    setQuestions((prev) => prev.map((q) => (q.id === id ? { ...q, ...updates } : q)));
  }

  function handleDeleteQuestion(id: number) {
    setQuestions((prev) => prev.filter((q) => q.id !== id));
  }

  async function handleRegenerateQuestion(qId: number, extraRequirements: string) {
    const q = questions.find((q) => q.id === qId);
    if (!q) return;

    setRegeneratingIds((s) => new Set(s).add(String(qId)));
    try {
      const body: RegenerateQuestionBody = {
        qId,
        qType: q.type,
        objective: q.objective,
        entityName: q.entity ?? "",
        extraRequirements: extraRequirements || undefined,
        currentQuestion: q.question || undefined,
      };
      const res = await fetch(
        `/api/v1/courses/${courseId}/assignments/${assignmentId}/regenerate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify(body),
        },
      );
      if (res.ok) {
        const d = (await res.json()) as { question: QuestionItem };
        setQuestions((prev) => prev.map((q) => (q.id === qId ? { ...d.question, score: q.score } : q)));
        notify("success", "题目已重新生成");
      } else {
        const d = (await res.json()) as { error?: { message: string } };
        notify("error", d.error?.message ?? "重新生成失败");
      }
    } catch {
      notify("error", "网络错误，请重试");
    } finally {
      setRegeneratingIds((s) => {
        const next = new Set(s);
        next.delete(String(qId));
        return next;
      });
    }
  }

  // ── Add custom question ────────────────────────────────────────────────────
  async function handlePreviewQuestion(
    body: Omit<CompleteQuestionBody, "score">,
  ): Promise<QuestionItem> {
    const res = await fetch(
      `/api/v1/courses/${courseId}/assignments/${assignmentId}/preview-question`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(body),
      },
    );
    const d = await res.json() as { question?: QuestionItem; error?: { message: string } };
    if (!res.ok) throw new Error(d.error?.message ?? "AI 补全失败，请重试");
    return d.question!;
  }

  function handleAddQuestion(question: QuestionItem, score: number) {
    setQuestions((prev) => [...prev, { ...question, score }]);
    setAddDialogOpen(false);
    notify("success", "题目已添加，点击「保存草稿」以保存");
  }

  // ── Save (PATCH) ───────────────────────────────────────────────────────────
  async function handleSave() {
    setSaving(true);
    try {
      const res = await fetch(
        `/api/v1/courses/${courseId}/assignments/${assignmentId}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ title, questions }),
        },
      );
      if (res.ok) {
        notify("success", "已保存");
        void load();
      } else {
        const d = (await res.json()) as { error?: { message: string } };
        notify("error", d.error?.message ?? "保存失败");
      }
    } finally {
      setSaving(false);
    }
  }

  // ── Publish ────────────────────────────────────────────────────────────────
  async function handlePublish() {
    if (!confirm("发布后学生可见，确认发布？")) return;
    setPublishing(true);
    try {
      const res = await fetch(
        `/api/v1/courses/${courseId}/assignments/${assignmentId}/publish`,
        { method: "POST", credentials: "include" },
      );
      if (res.ok) {
        notify("success", "作业已发布！");
        void load();
      } else {
        const d = (await res.json()) as { error?: { message: string } };
        notify("error", d.error?.message ?? "发布失败");
      }
    } finally {
      setPublishing(false);
    }
  }

  const isDraft = assignment?.status === AssignmentStatus.DRAFT;
  const isGenerating = assignment?.status === AssignmentStatus.GENERATING;

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-6 space-y-6">
      {/* Notification toast */}
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

      {/* Back + header */}
      <div className="flex items-center gap-3">
        <Link
          href={`/courses/${courseId}/assignments`}
          className="text-muted-foreground hover:text-foreground"
        >
          <ChevronLeft size={20} />
        </Link>
        <FileQuestion size={18} className="text-primary shrink-0" />
        {loading ? (
          <Skeleton className="h-6 w-48" />
        ) : (
          <h1 className="text-lg font-semibold flex-1 truncate">{assignment?.title}</h1>
        )}
        {!loading && assignment && (
          <span
            className={cn(
              "text-xs rounded-full px-2.5 py-0.5 font-semibold shrink-0",
              assignment.status === "DRAFT" && "bg-yellow-100 text-yellow-700",
              assignment.status === "PUBLISHED" && "bg-green-100 text-green-700",
              assignment.status === "GENERATING" && "bg-blue-100 text-blue-700",
              assignment.status === "FAILED" && "bg-red-100 text-red-700",
            )}
          >
            {isGenerating && <Loader2 size={10} className="inline animate-spin mr-1" />}
            {STATUS_LABELS[assignment.status] ?? assignment.status}
          </span>
        )}
      </div>

      {loading && (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-32 rounded-lg" />)}
        </div>
      )}

      {!loading && assignment?.status === "GENERATING" && (
        <div className="flex flex-col items-center justify-center py-16 gap-3 text-muted-foreground">
          <Loader2 size={36} className="animate-spin text-primary" />
          <p className="text-sm font-medium">AI 正在生成作业，请稍候…</p>
          <p className="text-xs">通常需要 30–120 秒，页面将自动刷新</p>
        </div>
      )}

      {!loading && assignment?.status === "FAILED" && (
        <div className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4">
          <AlertCircle size={18} className="text-destructive shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-semibold text-destructive">生成失败</p>
            <p className="text-sm text-muted-foreground mt-1">{assignment.errorMessage ?? "未知错误"}</p>
          </div>
        </div>
      )}

      {!loading && (assignment?.status === "DRAFT" || assignment?.status === "PUBLISHED") && (
        <>
          {/* Quality Report */}
          {assignment.qualityReport && (
            <div className="rounded-lg border bg-muted/30 p-4 space-y-1">
              <div className="flex items-center gap-2 text-sm font-semibold">
                <Star size={14} className="text-yellow-500" />
                质量报告
                <span className="ml-auto text-base font-bold text-primary">
                  {assignment.qualityReport.overall_score} / 10
                </span>
              </div>
              {assignment.qualityReport.summary && (
                <p className="text-xs text-muted-foreground">{assignment.qualityReport.summary}</p>
              )}
              {assignment.qualityReport.question_reviews?.some((r) => r.issues.length > 0) && (
                <ul className="text-xs text-muted-foreground list-disc list-inside space-y-0.5 mt-1">
                  {assignment.qualityReport.question_reviews
                    ?.flatMap((r) => r.issues)
                    .filter(Boolean)
                    .map((issue, i) => (
                    <li key={i}>{issue}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* Title edit (draft only) */}
          {isDraft && (
            <div>
              <label className="text-xs font-medium text-muted-foreground mb-1 block">作业标题</label>
              <Input value={title} onChange={(e) => setTitle(e.target.value)} />
            </div>
          )}

          {/* Questions */}
          {questions.length === 0 ? (
            <div className="flex flex-col items-center py-10 text-muted-foreground gap-2">
              <FileQuestion size={32} className="opacity-30" />
              <p className="text-sm">暂无题目</p>
            </div>
          ) : (
            <DndContext
              sensors={sensors}
              collisionDetection={closestCenter}
              onDragEnd={handleDragEnd}
            >
              <SortableContext
                items={questions.map((q) => String(q.id))}
                strategy={verticalListSortingStrategy}
              >
                <div className="space-y-3">
                  {questions.map((q, i) => (
                    <QuestionCard
                      key={q.id}
                      question={q}
                      index={i}
                      onUpdate={handleUpdateQuestion}
                      onDelete={handleDeleteQuestion}
                      onRegenerate={handleRegenerateQuestion}
                      regenerating={regeneratingIds.has(String(q.id))}
                    />
                  ))}
                </div>
              </SortableContext>
            </DndContext>
          )}

          {/* Add custom question (draft only) */}
          {isDraft && (
            <>
              <button
                type="button"
                onClick={() => setAddDialogOpen(true)}
                className="w-full flex items-center justify-center gap-2 rounded-lg border border-dashed border-border py-3 text-sm text-muted-foreground hover:border-primary hover:text-primary transition-colors"
              >
                <Plus size={15} />
                添加自定义题目
              </button>
              <AddQuestionDialog
                open={addDialogOpen}
                onOpenChange={setAddDialogOpen}
                onPreview={handlePreviewQuestion}
                onAdd={handleAddQuestion}
              />
            </>
          )}

          {isDraft && (
            <div className="flex items-center justify-between pt-2 border-t">
              <p className="text-xs text-muted-foreground flex items-center gap-1">
                <Clock size={12} />
                {assignment.deadline
                  ? `截止 ${new Date(assignment.deadline).toLocaleDateString("zh-CN")}`
                  : "无截止时间"}
              </p>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void handleSave()}
                  disabled={saving}
                  className="gap-1.5"
                >
                  {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                  保存草稿
                </Button>
                <Button
                  size="sm"
                  onClick={() => void handlePublish()}
                  disabled={publishing || questions.length === 0}
                  className="gap-1.5"
                >
                  {publishing ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
                  发布
                </Button>
              </div>
            </div>
          )}

          {/* Published info */}
          {assignment.status === "PUBLISHED" && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground pt-2 border-t">
              <CheckCircle2 size={14} className="text-green-600" />
              已于 {assignment.publishedAt ? new Date(assignment.publishedAt).toLocaleString("zh-CN") : ""} 发布
            </div>
          )}
        </>
      )}
    </div>
  );
}
