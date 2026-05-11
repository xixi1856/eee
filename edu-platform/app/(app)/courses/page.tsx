"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { Plus, BookOpen, ChevronRight, Clock, Archive } from "lucide-react";
import { cn } from "@/lib/utils";

type CourseRow = {
  id: string;
  name: string;
  description?: string | null;
  status: string;
  created_at?: string;
};

type UserInfo = { role?: string };

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; className: string }> = {
    PUBLISHED: { label: "已发布", className: "status-published" },
    DRAFT: { label: "草稿", className: "status-draft" },
    ARCHIVED: { label: "已归档", className: "status-archived" },
  };
  const item = map[status] ?? { label: status, className: "status-archived" };
  return (
    <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold", item.className)}>
      {item.label}
    </span>
  );
}

function CourseCardSkeleton() {
  return (
    <div className="rounded-xl border border-border bg-card p-5 space-y-3">
      <Skeleton className="h-5 w-3/4" />
      <Skeleton className="h-3 w-1/3" />
      <Skeleton className="h-3 w-full" />
      <Skeleton className="h-3 w-4/5" />
    </div>
  );
}

export default function CoursesPage() {
  const router = useRouter();
  const [courses, setCourses] = useState<CourseRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [shareCode, setShareCode] = useState("");
  const [joining, setJoining] = useState(false);
  const [joinErr, setJoinErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [res, userRes] = await Promise.all([
        fetch("/api/v1/courses", { credentials: "include" }),
        fetch("/api/v1/user", { credentials: "include" }),
      ]);
      if (cancelled) return;
      if (!res.ok) {
        setErr(`加载失败 (${res.status})`);
        setCourses([]);
        return;
      }
      const data = (await res.json()) as { courses?: CourseRow[] };
      setCourses(data.courses ?? []);
      if (userRes.ok) {
        const u = (await userRes.json()) as UserInfo;
        setRole(u.role ?? null);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  async function joinByShareCode() {
    setJoinErr(null);
    setJoining(true);
    try {
      const res = await fetch("/api/v1/courses/join-by-code", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ share_code: shareCode }),
      });
      const data = (await res.json()) as {
        course_id?: string;
        error?: { message?: string };
      };
      if (!res.ok) {
        setJoinErr(data.error?.message ?? `加入失败 (${res.status})`);
        return;
      }
      if (data.course_id) {
        router.push(`/courses/${data.course_id}`);
      }
    } finally {
      setJoining(false);
    }
  }

  return (
    <div className="flex flex-col h-full overflow-auto">
      <div className="max-w-5xl mx-auto w-full px-6 py-8 space-y-8">
        {/* Header */}
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="font-display text-2xl font-semibold tracking-tight text-foreground">
              课程空间
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              {role === "TEACHER"
                ? "管理你创建或协作的课程"
                : "浏览并加入你的课程"}
            </p>
          </div>
          {role === "TEACHER" && (
            <Button asChild className="shrink-0">
              <Link href="/courses/create">
                <Plus size={16} className="mr-1.5" />
                创建课程
              </Link>
            </Button>
          )}
        </div>

        {(role === "STUDENT" || role === "TEACHER") && (
          <div className="rounded-xl border border-border bg-card p-4 flex flex-col sm:flex-row sm:items-end gap-3">
            <div className="flex-1 space-y-1.5 min-w-0">
              <label htmlFor="course-share-code" className="text-sm font-medium text-foreground">
                通过分享码加入课程
              </label>
              <Input
                id="course-share-code"
                placeholder="输入教师提供的分享码"
                value={shareCode}
                onChange={(e) => setShareCode(e.target.value)}
                className="font-mono tracking-wide max-w-md"
                autoComplete="off"
              />
            </div>
            <Button
              type="button"
              disabled={joining || !shareCode.trim()}
              onClick={() => void joinByShareCode()}
              className="shrink-0 w-full sm:w-auto"
            >
              {joining ? "加入中…" : "加入"}
            </Button>
          </div>
        )}
        {joinErr && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/8 px-4 py-3 text-sm text-destructive">
            {joinErr}
          </div>
        )}

        {/* Error */}
        {err && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/8 px-4 py-3 text-sm text-destructive">
            {err}
          </div>
        )}

        {/* Loading skeletons */}
        {!courses && !err && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <CourseCardSkeleton key={i} />
            ))}
          </div>
        )}

        {/* Empty state */}
        {courses?.length === 0 && (
          <div className="flex flex-col items-center justify-center py-24 rounded-xl border border-dashed border-border text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-full bg-muted mb-4">
              <BookOpen size={24} className="text-muted-foreground" />
            </div>
            <p className="text-base font-medium text-foreground mb-1">暂无课程</p>
            <p className="text-sm text-muted-foreground max-w-xs">
              {role === "TEACHER"
                ? "点击右上角「创建课程」开始构建你的第一门课程。"
                : "在上方输入教师提供的分享码加入课程，或联系教师获取分享码。"}
            </p>
          </div>
        )}

        {/* Course grid */}
        {courses && courses.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {courses.map((c) => (
              <Link
                key={c.id}
                href={`/courses/${c.id}`}
                className="group relative flex flex-col rounded-xl border border-border bg-card p-5 transition-all hover:border-primary/40 hover:shadow-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {/* Status + archived icon */}
                <div className="mb-3 flex items-center justify-between">
                  <StatusBadge status={c.status} />
                  {c.status === "ARCHIVED" && (
                    <Archive size={13} className="text-muted-foreground/60" />
                  )}
                </div>

                {/* Title */}
                <h2 className="font-display text-base font-semibold text-foreground line-clamp-2 leading-snug mb-2 group-hover:text-primary transition-colors">
                  {c.name}
                </h2>

                {/* Description */}
                <p className="text-[13px] text-muted-foreground line-clamp-3 leading-relaxed flex-1">
                  {c.description ?? "暂无课程描述"}
                </p>

                {/* Footer */}
                <div className="mt-4 flex items-center justify-between text-xs text-muted-foreground">
                  {c.created_at ? (
                    <span className="flex items-center gap-1">
                      <Clock size={11} />
                      {new Date(c.created_at).toLocaleDateString("zh-CN")}
                    </span>
                  ) : <span />}
                  <span className="flex items-center gap-0.5 font-medium text-primary opacity-0 group-hover:opacity-100 transition-opacity">
                    进入 <ChevronRight size={13} />
                  </span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
