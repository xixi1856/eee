"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  BookOpen, ChevronLeft, FileText, GraduationCap, LayoutList,
  MessageSquare, BarChart3, Loader2, Trash2, AlertCircle,
  CheckCircle2, Clock, Cpu, BookMarked, Pencil, FileQuestion, Sparkles, Copy,
} from "lucide-react";
import type { AssignmentSummaryDto } from "@/lib/dto/assignment.dto";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import MaterialUpload from "@/components/MaterialUpload";
import { RetryMaterialIndexPopover } from "@/components/RetryMaterialIndexPopover";
import KnowledgeGraphPanel from "@/components/KnowledgeGraphPanel";
import { cn } from "@/lib/utils";

type Course = {
  id: string; name: string; description: string | null;
  cover_image_url: string | null; status: string;
  share_code?: string | null;
};
type Material = {
  id: string; filename: string; file_type: string;
  lesson_id: string | null;
  status: string;
  preview_pdf_status: "NA" | "PENDING" | "READY" | "FAILED";
  indexed_chunk_count: number; status_message: string | null;
};
type Lesson = {
  id: string; title: string; description: string | null; order_index: number;
};

type KnowledgeAnalyticsData = {
  high_frequency_questions: {
    question: string;
    frequency: number;
    related_knowledge_points: string[];
  }[];
  error_prone_knowledge_points: {
    knowledge_point: string;
    error_rate: number;
    error_count: number;
  }[];
  knowledge_heatmap: {
    knowledge_point: string;
    heat_score: number;
  }[];
};

type Tab = "overview" | "materials" | "lessons" | "assignments" | "analytics";

const ASSIGNMENT_STATUS_LABELS: Record<string, string> = {
  GENERATING: "生成中", FAILED: "失败", DRAFT: "草稿", PUBLISHED: "已发布", ARCHIVED: "已归档",
};
const ASSIGNMENT_STATUS_CLASSES: Record<string, string> = {
  GENERATING: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300",
  FAILED: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300",
  DRAFT: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300",
  PUBLISHED: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300",
  ARCHIVED: "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400",
};
function AssignmentStatusBadge({ status }: { status: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold", ASSIGNMENT_STATUS_CLASSES[status] ?? "")}>
      {status === "GENERATING" && <Loader2 size={10} className="animate-spin" />}
      {status === "FAILED" && <AlertCircle size={10} />}
      {status === "PUBLISHED" && <CheckCircle2 size={10} />}
      {status === "DRAFT" && <Clock size={10} />}
      {ASSIGNMENT_STATUS_LABELS[status] ?? status}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    PUBLISHED: "status-published", DRAFT: "status-draft", ARCHIVED: "status-archived",
  };
  const labelMap: Record<string, string> = {
    PUBLISHED: "已发布", DRAFT: "草稿", ARCHIVED: "已归档",
  };
  return (
    <span className={cn("inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold", map[status] ?? "status-archived")}>
      {labelMap[status] ?? status}
    </span>
  );
}

function MaterialStatusIcon({ status }: { status: string }) {
  if (status === "READY") return <CheckCircle2 size={14} className="text-[oklch(0.55_0.12_145)]" />;
  if (status === "FAILED") return <AlertCircle size={14} className="text-destructive" />;
  if (["PARSING", "PARSED", "INDEXING"].includes(status)) return <Loader2 size={14} className="animate-spin text-[oklch(0.55_0.12_250)]" />;
  return <Clock size={14} className="text-muted-foreground" />;
}

function MaterialStatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    READY: "status-ready", FAILED: "status-failed",
    PARSING: "status-processing", PARSED: "status-processing",
    INDEXING: "status-processing", UPLOADED: "status-draft",
  };
  const labelMap: Record<string, string> = {
    READY: "就绪", FAILED: "失败", PARSING: "解析中",
    PARSED: "已解析", INDEXING: "索引中", UPLOADED: "已上传",
  };
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold", map[status] ?? "")}>
      <MaterialStatusIcon status={status} />
      {labelMap[status] ?? status}
    </span>
  );
}

function MaterialRow({
  m,
  isTeacher,
  onDelete,
  courseId,
  onMaterialsRefresh,
  onNotify,
  indent = false,
}: {
  m: Material;
  isTeacher: boolean;
  onDelete: (id: string) => void;
  courseId: string;
  onMaterialsRefresh: () => void;
  onNotify: (type: "success" | "error", msg: string) => void;
  indent?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 bg-card px-4 py-3 hover:bg-muted/20 transition-colors",
        indent && "pl-6",
      )}
    >
      <div className="flex items-center gap-3 min-w-0">
        <FileText size={15} className="shrink-0 text-muted-foreground" />
        <div className="min-w-0">
          <p className="text-sm font-medium text-foreground truncate">{m.filename}</p>
          <div className="flex items-center gap-2 mt-0.5">
            <span className="text-[11px] text-muted-foreground uppercase font-mono">{m.file_type}</span>
            {m.indexed_chunk_count > 0 && (
              <span className="text-[11px] text-muted-foreground">{m.indexed_chunk_count} chunks</span>
            )}
          </div>
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <MaterialStatusBadge status={m.status} />
        {isTeacher && m.status === "FAILED" && (
          <RetryMaterialIndexPopover
            courseId={courseId}
            materialId={m.id}
            iconSize={13}
            triggerClassName="flex h-7 w-7 items-center justify-center rounded-lg"
            onSuccess={() => onMaterialsRefresh()}
            onError={(msg) => onNotify("error", msg)}
          />
        )}
        {isTeacher && (
          <button
            type="button"
            onClick={() => onDelete(m.id)}
            className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors"
          >
            <Trash2 size={13} />
          </button>
        )}
      </div>
    </div>
  );
}

export default function CourseDetailPage() {
  const params = useParams();
  const router = useRouter();
  const courseId = typeof params?.courseId === "string" ? params.courseId : null;
  const [course, setCourse] = useState<Course | null>(null);
  const [materials, setMaterials] = useState<Material[]>([]);
  const [lessons, setLessons] = useState<Lesson[]>([]);
  const [role, setRole] = useState<string | null>(null);
  const [accessError, setAccessError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("overview");
  const [notification, setNotification] = useState<{type:"success"|"error"; msg:string} | null>(null);
  const [assignments, setAssignments] = useState<AssignmentSummaryDto[]>([]);
  const [assignmentsLoaded, setAssignmentsLoaded] = useState(false);
  const [analyticsData, setAnalyticsData] = useState<KnowledgeAnalyticsData | null>(null);
  const [analyticsLoaded, setAnalyticsLoaded] = useState(false);
  const [analyticsRange, setAnalyticsRange] = useState<"7d" | "30d" | "all">("7d");

  function notify(type: "success" | "error", msg: string) {
    setNotification({ type, msg });
    setTimeout(() => setNotification(null), 3000);
  }

  const load = useCallback(async (cid: string) => {
    const [cRes, mRes, uRes, lRes] = await Promise.all([
      fetch(`/api/v1/courses/${cid}`, { credentials: "include" }),
      fetch(`/api/v1/courses/${cid}/materials`, { credentials: "include" }),
      fetch("/api/v1/user", { credentials: "include" }),
      fetch(`/api/v1/courses/${cid}/lessons`, { credentials: "include" }),
    ]);
    if (cRes.ok) {
      setAccessError(null);
      const j = (await cRes.json()) as { course?: Course };
      setCourse(j.course ?? null);
    } else {
      setAccessError("无权查看该课程或未登录");
      setCourse(null);
    }
    if (mRes.ok) {
      const j = (await mRes.json()) as { materials?: Material[] };
      setMaterials(j.materials ?? []);
    }
    if (uRes.ok) {
      const j = (await uRes.json()) as { role?: string };
      setRole(j.role ?? null);
    }
    if (lRes.ok) {
      const j = (await lRes.json()) as { lessons?: Lesson[] };
      setLessons((j.lessons ?? []).sort((a, b) => a.order_index - b.order_index));
    }
  }, []);

  useEffect(() => {
    if (!courseId) return;
    void load(courseId);
    const t = setInterval(() => void load(courseId), 4000);
    return () => clearInterval(t);
  }, [courseId, load]);

  async function doAction(action: "publish" | "archive") {
    if (!courseId) return;
    const urlMap = { publish: "publish", archive: "archive" };
    const res = await fetch(`/api/v1/courses/${courseId}/${urlMap[action]}`, {
      method: "POST", credentials: "include",
    });
    if (!res.ok) { notify("error", `操作失败`); return; }
    const msgMap = { publish: "课程已发布", archive: "课程已归档" };
    notify("success", msgMap[action]);
    void load(courseId);
  }

  // Lazy-load assignments when tab is first activated
  useEffect(() => {
    if (activeTab !== "assignments" || !courseId || assignmentsLoaded) return;
    void fetch(`/api/v1/courses/${courseId}/assignments`, { credentials: "include" })
      .then((r) => r.json())
      .then((d: { assignments?: AssignmentSummaryDto[] }) => {
        setAssignments(d.assignments ?? []);
        setAssignmentsLoaded(true);
      });
  }, [activeTab, courseId, assignmentsLoaded]);

  // Poll while any assignment is GENERATING
  useEffect(() => {
    if (activeTab !== "assignments" || !courseId) return;
    if (!assignments.some((a) => a.status === "GENERATING")) return;
    const id = setInterval(() => {
      void fetch(`/api/v1/courses/${courseId}/assignments`, { credentials: "include" })
        .then((r) => r.json())
        .then((d: { assignments?: AssignmentSummaryDto[] }) => {
          setAssignments(d.assignments ?? []);
        });
    }, 5000);
    return () => clearInterval(id);
  }, [activeTab, courseId, assignments]);

  // Lazy-load analytics when tab is activated; reload when range changes
  useEffect(() => {
    if (activeTab !== "analytics" || !courseId || role !== "TEACHER" || analyticsLoaded) return;
    void fetch(`/api/v1/courses/${courseId}/analytics/knowledge?range=${analyticsRange}`, { credentials: "include" })
      .then((r) => r.json())
      .then((d: KnowledgeAnalyticsData) => {
        setAnalyticsData(d);
        setAnalyticsLoaded(true);
      })
      .catch(() => setAnalyticsLoaded(true));
  }, [activeTab, courseId, role, analyticsLoaded, analyticsRange]);

  async function deleteMaterial(mid: string) {
    if (!courseId) return;
    if (!window.confirm("确定删除该资料？此操作不可撤销。")) return;
    const res = await fetch(`/api/v1/materials/${mid}`, {
      method: "DELETE", credentials: "include",
    });
    if (res.ok) { notify("success", "资料已删除"); void load(courseId); }
    else notify("error", "删除失败");
  }

  if (!courseId) return <div className="p-6 text-muted-foreground">无效的课程链接</div>;

  if (accessError) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-4 text-muted-foreground">
        <AlertCircle size={32} className="text-destructive" />
        <p className="text-sm">{accessError}</p>
        <Button variant="outline" size="sm" asChild><Link href="/courses">返回课程列表</Link></Button>
      </div>
    );
  }

  const isTeacher = role === "TEACHER";
  const isStudent = role === "STUDENT";
  const tabs: { id: Tab; label: string; icon: typeof BookOpen }[] = [
    { id: "overview", label: "概览", icon: BookOpen },
    { id: "materials", label: "材料", icon: FileText },
    { id: "lessons", label: "课时", icon: LayoutList },
    { id: "assignments", label: "作业", icon: FileQuestion },
    ...(isTeacher ? [{ id: "analytics" as Tab, label: "分析", icon: BarChart3 }] : []),
  ];

  return (
    <div className="flex flex-col h-full overflow-auto">
      {/* Notification bar */}
      {notification && (
        <div className={cn(
          "fixed top-4 left-1/2 -translate-x-1/2 z-50 flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium shadow-lg transition-all",
          notification.type === "success"
            ? "bg-[oklch(0.92_0.08_145)] text-[oklch(0.35_0.10_145)]"
            : "bg-destructive text-destructive-foreground"
        )}>
          {notification.type === "success" ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}
          {notification.msg}
        </div>
      )}

      <div className="max-w-4xl mx-auto w-full px-6 py-8 space-y-6">
        {/* Breadcrumb */}
        <Link href="/courses" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
          <ChevronLeft size={15} />
          课程列表
        </Link>

        {/* Course header */}
        {!course ? (
          <div className="space-y-2">
            <Skeleton className="h-7 w-2/3" />
            <Skeleton className="h-4 w-1/3" />
          </div>
        ) : (
          <div className="flex items-start justify-between gap-4">
            <div className="space-y-1.5 min-w-0">
              <div className="flex items-center gap-2.5 flex-wrap">
                <h1 className="font-display text-2xl font-semibold text-foreground tracking-tight truncate">
                  {course.name}
                </h1>
                <StatusBadge status={course.status} />
              </div>
              {course.description && (
                <p className="text-sm text-muted-foreground">{course.description}</p>
              )}
              {isTeacher && course.status === "PUBLISHED" && course.share_code && (
                <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-muted/25 px-3 py-2 text-sm">
                  <span className="text-muted-foreground shrink-0">课程分享码</span>
                  <code className="font-mono text-sm font-semibold tracking-widest text-foreground">
                    {course.share_code}
                  </code>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-7 gap-1 px-2"
                    onClick={() => {
                      void navigator.clipboard.writeText(course.share_code ?? "").then(() => {
                        notify("success", "分享码已复制");
                      });
                    }}
                  >
                    <Copy size={13} />
                    复制
                  </Button>
                </div>
              )}
              {isStudent && (
                <p className="mt-2 text-xs text-muted-foreground">
                  加入新课程请在「课程列表」页输入教师提供的分享码。
                </p>
              )}
            </div>
            {/* Action buttons */}
            <div className="flex items-center gap-2 shrink-0 flex-wrap">
              {isTeacher && course.status === "DRAFT" && (
                <Button size="sm" onClick={() => void doAction("publish")}>发布课程</Button>
              )}
              {isTeacher && course.status === "PUBLISHED" && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    if (!window.confirm("确定将此课程归档？归档后学生可能无法访问此课程。")) return;
                    void doAction("archive");
                  }}
                >归档</Button>
              )}
              {(isStudent || isTeacher) && course.status === "PUBLISHED" && (
                <Button size="sm" variant="outline" asChild>
                  <Link href={`/courses/${courseId}/chat`}>
                    <MessageSquare size={14} className="mr-1.5" />课程问答
                  </Link>
                </Button>
              )}
              {isTeacher && (
                <>
                  <Button size="sm" variant="ghost" asChild>
                    <Link href={`/courses/${courseId}/analytics`}>
                      <BarChart3 size={14} className="mr-1.5" />数据
                    </Link>
                  </Button>
                  <Button size="sm" variant="ghost" asChild>
                    <Link href={`/courses/${courseId}/edit`}>
                      <Pencil size={14} className="mr-1.5" />编辑
                    </Link>
                  </Button>
                </>
              )}
            </div>
          </div>
        )}

        {/* Tabs */}
        <div className="border-b border-border">
          <div className="flex gap-0">
            {tabs.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                onClick={() => setActiveTab(id)}
                className={cn(
                  "flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors",
                  activeTab === id
                    ? "border-primary text-primary"
                    : "border-transparent text-muted-foreground hover:text-foreground hover:border-border"
                )}
              >
                <Icon size={14} />
                {label}
                {id === "materials" && materials.length > 0 && (
                  <span className="ml-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground">
                    {materials.length}
                  </span>
                )}
                {id === "lessons" && lessons.length > 0 && (
                  <span className="ml-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground">
                    {lessons.length}
                  </span>
                )}
                {id === "assignments" && assignmentsLoaded && assignments.length > 0 && (
                  <span className="ml-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground">
                    {assignments.length}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>

        {/* Tab panels */}

        {/* Overview */}
        {activeTab === "overview" && (
          <div className="space-y-6">
            {!course ? (
              <div className="space-y-3">
                {Array.from({length:3}).map((_,i) => <Skeleton key={i} className="h-4 w-full" />)}
              </div>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  {[
                    { icon: GraduationCap, label: "课程状态", value: { PUBLISHED:"已发布", DRAFT:"草稿", ARCHIVED:"已归档" }[course.status] ?? course.status },
                    { icon: FileText, label: "资料数量", value: `${materials.length} 份` },
                    { icon: BookMarked, label: "就绪资料", value: `${materials.filter(m=>m.status==="READY").length} 份` },
                    { icon: Cpu, label: "索引块总数", value: materials.reduce((s,m)=>s+m.indexed_chunk_count,0) },
                  ].map(({ icon: Icon, label, value }) => (
                    <div key={label} className="rounded-xl border border-border bg-card p-4 space-y-1">
                      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <Icon size={12} />
                        {label}
                      </div>
                      <div className="text-lg font-semibold text-foreground font-ui">{value}</div>
                    </div>
                  ))}
                </div>

                {course.description && (
                  <div className="rounded-xl border border-border bg-card p-5">
                    <h3 className="text-sm font-medium text-foreground mb-2">课程简介</h3>
                    <p className="text-sm text-muted-foreground leading-relaxed">{course.description}</p>
                  </div>
                )}

                <KnowledgeGraphPanel courseId={courseId!} isTeacher={isTeacher} />

                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" size="sm" asChild>
                    <Link href={`/courses/${courseId}/lessons`}>
                      <LayoutList size={14} className="mr-1.5" />管理课时
                    </Link>
                  </Button>
                </div>
              </>
            )}
          </div>
        )}

        {/* Materials */}
        {activeTab === "materials" && (
          <div className="space-y-6">
            {lessons.length === 0 ? (
              /* No lessons yet — show a prompt and a fallback upload area */
              <div className="space-y-4">
                <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 dark:border-amber-900/50 dark:bg-amber-950/20 px-4 py-3 text-sm">
                  <AlertCircle size={15} className="mt-0.5 shrink-0 text-amber-500" />
                  <div className="space-y-1">
                    <p className="font-medium text-foreground">尚未创建课时</p>
                    <p className="text-xs text-muted-foreground">
                      建议先创建课时，再将资料归属到对应课时。
                    </p>
                    {isTeacher && (
                      <Link
                        href={`/courses/${courseId}/lessons`}
                        className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline mt-0.5"
                      >
                        <LayoutList size={12} />
                        前往管理课时
                      </Link>
                    )}
                  </div>
                </div>
                {isTeacher && courseId && (
                  <div className="rounded-xl border border-dashed border-border bg-muted/30 p-4">
                    <p className="text-xs text-muted-foreground mb-2">上传资料（未分配课时）</p>
                    <MaterialUpload courseId={courseId} onUploaded={() => void load(courseId)} />
                  </div>
                )}
                {materials.length > 0 && (
                  <div className="space-y-2">
                    {materials.map((m) => (
                      <MaterialRow
                        key={m.id}
                        m={m}
                        isTeacher={isTeacher}
                        onDelete={deleteMaterial}
                        courseId={courseId}
                        onMaterialsRefresh={() => void load(courseId)}
                        onNotify={notify}
                      />
                    ))}
                  </div>
                )}
              </div>
            ) : (
              /* Lessons exist — group materials under each lesson */
              <div className="space-y-5">
                {lessons.map((lesson, idx) => {
                  const lessonMaterials = materials.filter((m) => m.lesson_id === lesson.id);
                  return (
                    <div key={lesson.id} className="rounded-xl border border-border bg-card overflow-hidden">
                      {/* Lesson header */}
                      <div className="flex items-center gap-3 px-4 py-3 border-b border-border bg-muted/30">
                        <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary text-xs font-bold">
                          {idx + 1}
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-semibold text-foreground truncate">{lesson.title}</p>
                          {lesson.description && (
                            <p className="text-xs text-muted-foreground truncate">{lesson.description}</p>
                          )}
                        </div>
                        <span className="text-xs text-muted-foreground shrink-0">
                          {lessonMaterials.length} 份资料
                        </span>
                      </div>

                      {/* Materials list for this lesson */}
                      <div className="divide-y divide-border">
                        {lessonMaterials.length === 0 && !isTeacher && (
                          <div className="px-4 py-6 text-center text-xs text-muted-foreground">
                            本课时暂无资料
                          </div>
                        )}
                        {lessonMaterials.map((m) => (
                          <MaterialRow
                            key={m.id}
                            m={m}
                            isTeacher={isTeacher}
                            onDelete={deleteMaterial}
                            courseId={courseId}
                            onMaterialsRefresh={() => void load(courseId)}
                            onNotify={notify}
                            indent
                          />
                        ))}
                      </div>

                      {/* Teacher upload area for this lesson */}
                      {isTeacher && courseId && (
                        <div className="px-4 py-3 border-t border-dashed border-border bg-muted/10">
                          <MaterialUpload
                            courseId={courseId}
                            lessonId={lesson.id}
                            onUploaded={() => void load(courseId)}
                          />
                        </div>
                      )}
                    </div>
                  );
                })}

                {/* Unassigned materials section */}
                {(() => {
                  const unassigned = materials.filter((m) => m.lesson_id === null);
                  if (unassigned.length === 0 && !isTeacher) return null;
                  return (
                    <div className="rounded-xl border border-dashed border-border overflow-hidden">
                      <div className="flex items-center gap-2 px-4 py-3 border-b border-dashed border-border bg-muted/20">
                        <FileText size={14} className="text-muted-foreground" />
                        <p className="text-sm font-medium text-muted-foreground">未分配课时的资料</p>
                        {unassigned.length > 0 && (
                          <span className="ml-auto text-xs text-muted-foreground">{unassigned.length} 份</span>
                        )}
                      </div>
                      {unassigned.length > 0 && (
                        <div className="divide-y divide-border/50">
                          {unassigned.map((m) => (
                            <MaterialRow
                              key={m.id}
                              m={m}
                              isTeacher={isTeacher}
                              onDelete={deleteMaterial}
                              courseId={courseId}
                              onMaterialsRefresh={() => void load(courseId)}
                              onNotify={notify}
                              indent
                            />
                          ))}
                        </div>
                      )}
                      {isTeacher && courseId && (
                        <div className="px-4 py-3 border-t border-dashed border-border bg-muted/10">
                          <MaterialUpload courseId={courseId} onUploaded={() => void load(courseId)} />
                        </div>
                      )}
                    </div>
                  );
                })()}
              </div>
            )}
          </div>
        )}

        {/* Lessons */}
        {activeTab === "lessons" && (
          <div className="space-y-5">
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                {lessons.length > 0 ? `共 ${lessons.length} 个课时，按序号排列` : "暂无课时"}
              </p>
              {isTeacher && (
                <Button size="sm" asChild>
                  <Link href={`/courses/${courseId}/lessons`}>
                    <Pencil size={13} className="mr-1.5" />管理课时
                  </Link>
                </Button>
              )}
            </div>

            {lessons.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 rounded-xl border border-dashed border-border text-center">
                <LayoutList size={28} className="text-muted-foreground mb-3" />
                <p className="text-sm font-medium text-foreground mb-1">暂无课时</p>
                {isTeacher && (
                  <p className="text-xs text-muted-foreground">前往「管理课时」添加课时结构</p>
                )}
              </div>
            ) : (
              <div className="space-y-2">
                {lessons.map((l, idx) => (
                  <div key={l.id} className="flex items-start gap-3 rounded-xl border border-border bg-card px-4 py-3">
                    <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary text-xs font-bold mt-0.5">
                      {idx + 1}
                    </div>
                    <div>
                      <p className="text-sm font-medium text-foreground">{l.title}</p>
                      {l.description && (
                        <p className="text-xs text-muted-foreground mt-0.5">{l.description}</p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Assignments */}
        {activeTab === "assignments" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                {!assignmentsLoaded ? "加载中…"
                  : assignments.length > 0 ? `共 ${assignments.length} 份作业`
                  : "暂无作业"}
              </p>
              {isTeacher && (
                <Button
                  size="sm"
                  className="gap-1.5"
                  onClick={() => courseId && router.push(`/courses/${courseId}/assignments/new`)}
                >
                  <Sparkles size={14} />新建作业
                </Button>
              )}
            </div>

            {!assignmentsLoaded ? (
              <div className="space-y-3">
                {[1, 2, 3].map((i) => <Skeleton key={i} className="h-16 rounded-lg" />)}
              </div>
            ) : assignments.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 rounded-xl border border-dashed border-border text-center">
                <FileQuestion size={28} className="text-muted-foreground mb-3" />
                <p className="text-sm font-medium text-foreground mb-1">暂无作业</p>
                {isTeacher && (
                  <p className="text-xs text-muted-foreground">点击「新建作业」生成第一份作业</p>
                )}
              </div>
            ) : (
              <div className="space-y-2">
                {assignments.map((a) => (
                  <Link
                    key={a.id}
                    href={`/courses/${courseId}/assignments/${a.id}`}
                    className="flex items-center gap-4 rounded-xl border bg-card px-4 py-3 hover:bg-muted/20 transition-colors"
                  >
                    <FileQuestion size={16} className="shrink-0 text-muted-foreground" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium truncate">{a.title}</p>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {a.questionCount > 0 ? `${a.questionCount} 题` : "暂无题目"}
                        {a.qualityScore !== null && ` · ${a.qualityScore} 分`}
                        {a.deadline && ` · 截止 ${new Date(a.deadline).toLocaleDateString("zh-CN")}`}
                      </p>
                    </div>
                    <AssignmentStatusBadge status={a.status} />
                  </Link>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Analytics */}
        {activeTab === "analytics" && isTeacher && (
          <div className="space-y-6">
            {/* Time range selector */}
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                {!analyticsLoaded ? "加载中…" : "学习行为分析"}
              </p>
              <select
                value={analyticsRange}
                onChange={(e) => {
                  setAnalyticsRange(e.target.value as "7d" | "30d" | "all");
                  setAnalyticsLoaded(false);
                }}
                className="text-sm border border-border rounded-lg px-2.5 py-1.5 bg-background text-foreground cursor-pointer"
              >
                <option value="7d">最近 7 天</option>
                <option value="30d">最近 30 天</option>
                <option value="all">全部时间</option>
              </select>
            </div>

            {!analyticsLoaded ? (
              <div className="space-y-3">
                {[1, 2, 3].map((i) => <Skeleton key={i} className="h-20 rounded-xl" />)}
              </div>
            ) : (
              <>
                {/* High-frequency questions */}
                <section className="space-y-3">
                  <h2 className="font-display text-base font-semibold text-foreground flex items-center gap-2">
                    <MessageSquare size={15} className="text-primary" />
                    高频问题 Top 5
                  </h2>
                  {!analyticsData || analyticsData.high_frequency_questions.length === 0 ? (
                    <p className="text-sm text-muted-foreground py-4">暂无问答数据</p>
                  ) : (
                    <div className="space-y-2">
                      {analyticsData.high_frequency_questions.map((q, idx) => (
                        <div key={idx} className="rounded-xl border border-border bg-card px-4 py-3 space-y-2">
                          <div className="flex items-start justify-between gap-4">
                            <div className="flex items-start gap-3">
                              <span className="shrink-0 flex h-5 w-5 items-center justify-center rounded-full bg-primary/10 text-primary text-[10px] font-bold mt-0.5">
                                {idx + 1}
                              </span>
                              <p className="text-sm text-foreground leading-relaxed">{q.question}</p>
                            </div>
                            <span className="shrink-0 text-xs font-semibold text-muted-foreground whitespace-nowrap">{q.frequency} 次</span>
                          </div>
                          {q.related_knowledge_points.length > 0 && (
                            <div className="flex flex-wrap gap-1.5 pl-8">
                              {q.related_knowledge_points.map((kp, ki) => (
                                <span key={ki} className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground font-medium">
                                  {kp}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </section>

                {/* Knowledge heatmap */}
                <section className="space-y-3">
                  <h2 className="font-display text-base font-semibold text-foreground flex items-center gap-2">
                    <BarChart3 size={15} className="text-primary" />
                    知识点热度排行
                  </h2>
                  {!analyticsData || analyticsData.knowledge_heatmap.length === 0 ? (
                    <p className="text-sm text-muted-foreground py-4">暂无资料命中数据</p>
                  ) : (
                    <div className="rounded-xl border border-border bg-card divide-y divide-border overflow-hidden">
                      {(() => {
                        const maxScore = Math.max(...analyticsData.knowledge_heatmap.map((h) => h.heat_score), 1);
                        return analyticsData.knowledge_heatmap.map((item, idx) => (
                          <div key={idx} className="flex items-center gap-3 px-4 py-3">
                            <span className="text-xs text-muted-foreground w-4 shrink-0 text-right">{idx + 1}</span>
                            <div className="flex-1 min-w-0 space-y-1.5">
                              <p className="text-sm font-medium text-foreground truncate">{item.knowledge_point}</p>
                              <div className="h-1.5 w-full bg-muted rounded-full overflow-hidden">
                                <div
                                  className="h-full bg-primary/70 rounded-full"
                                  style={{ width: `${Math.round((item.heat_score / maxScore) * 100)}%` }}
                                />
                              </div>
                            </div>
                            <span className="text-xs font-semibold text-muted-foreground shrink-0">{item.heat_score} 次</span>
                          </div>
                        ));
                      })()}
                    </div>
                  )}
                </section>

                {/* Error-prone knowledge points (placeholder for future) */}
                <section className="space-y-3">
                  <h2 className="font-display text-base font-semibold text-foreground flex items-center gap-2">
                    <AlertCircle size={15} className="text-muted-foreground" />
                    易错知识点
                  </h2>
                  <div className="flex items-center gap-3 rounded-xl border border-dashed border-border bg-muted/20 px-4 py-5">
                    <AlertCircle size={16} className="text-muted-foreground/40 shrink-0" />
                    <p className="text-sm text-muted-foreground">待接入作业提交功能后自动统计易错知识点</p>
                  </div>
                </section>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
