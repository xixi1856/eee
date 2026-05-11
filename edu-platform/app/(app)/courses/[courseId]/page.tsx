"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  BookOpen, ChevronLeft, FileText, GraduationCap, LayoutList,
  MessageSquare, BarChart3, Loader2, Trash2, AlertCircle,
  CheckCircle2, Clock, Cpu, BookMarked, Pencil
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import MaterialUpload from "@/components/MaterialUpload";
import { cn } from "@/lib/utils";

type Course = {
  id: string; name: string; description: string | null;
  cover_image_url: string | null; status: string;
};
type Material = {
  id: string; filename: string; file_type: string;
  lesson_id: string | null;
  status: string; indexed_chunk_count: number; status_message: string | null;
};
type Lesson = {
  id: string; title: string; description: string | null; order_index: number;
};

type Tab = "overview" | "materials" | "lessons";

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
  indent = false,
}: {
  m: Material;
  isTeacher: boolean;
  onDelete: (id: string) => void;
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
        {isTeacher && (
          <button
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
  const courseId = typeof params?.courseId === "string" ? params.courseId : null;
  const [course, setCourse] = useState<Course | null>(null);
  const [materials, setMaterials] = useState<Material[]>([]);
  const [lessons, setLessons] = useState<Lesson[]>([]);
  const [role, setRole] = useState<string | null>(null);
  const [accessError, setAccessError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("overview");
  const [notification, setNotification] = useState<{type:"success"|"error"; msg:string} | null>(null);

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

  async function doAction(action: "publish" | "archive" | "join") {
    if (!courseId) return;
    const urlMap = { publish: "publish", archive: "archive", join: "join" };
    const res = await fetch(`/api/v1/courses/${courseId}/${urlMap[action]}`, {
      method: "POST", credentials: "include",
    });
    if (!res.ok) { notify("error", `操作失败`); return; }
    const msgMap = { publish: "课程已发布", archive: "课程已归档", join: "已加入课程" };
    notify("success", msgMap[action]);
    void load(courseId);
  }

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
            </div>
            {/* Action buttons */}
            <div className="flex items-center gap-2 shrink-0 flex-wrap">
              {isTeacher && course.status === "DRAFT" && (
                <Button size="sm" onClick={() => void doAction("publish")}>发布课程</Button>
              )}
              {isTeacher && course.status === "PUBLISHED" && (
                <Button size="sm" variant="outline" onClick={() => void doAction("archive")}>归档</Button>
              )}
              {isStudent && course.status === "PUBLISHED" && (
                <Button size="sm" onClick={() => void doAction("join")}>加入课程</Button>
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
                      <MaterialRow key={m.id} m={m} isTeacher={isTeacher} onDelete={deleteMaterial} />
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
                          <MaterialRow key={m.id} m={m} isTeacher={isTeacher} onDelete={deleteMaterial} indent />
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
                            <MaterialRow key={m.id} m={m} isTeacher={isTeacher} onDelete={deleteMaterial} indent />
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
      </div>
    </div>
  );
}
