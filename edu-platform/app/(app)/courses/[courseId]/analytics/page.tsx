"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ChevronLeft, AlertCircle, MessageSquare, Clock, Users, FileText } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";

type Analytics = {
  total_questions: number;
  avg_response_time_ms: number;
  top_questions: { question: string; count: number; avg_quality: number | null }[];
  active_students: {
    student_id: string;
    name: string | null;
    question_count: number;
    last_active: string;
  }[];
  top_materials: {
    material_id: string;
    title: string | null;
    hit_count: number;
  }[];
};

export default function CourseAnalyticsPage() {
  const params = useParams();
  const courseId = typeof params?.courseId === "string" ? params.courseId : null;
  const [data, setData] = useState<Analytics | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!courseId) return;
    void (async () => {
      const res = await fetch(`/api/v1/courses/${courseId}/analytics`, { credentials: "include" });
      if (!res.ok) {
        const j = (await res.json().catch(() => ({}))) as { error?: { message?: string } };
        setErr(j.error?.message ?? "无法加载统计（需教师或管理员权限）");
        return;
      }
      setData((await res.json()) as Analytics);
    })();
  }, [courseId]);

  if (!courseId) return (
    <div className="flex items-center justify-center h-full text-muted-foreground text-sm">无效的课程链接</div>
  );

  return (
    <div className="flex flex-col h-full overflow-auto">
      <div className="max-w-4xl mx-auto w-full px-6 py-8 space-y-8">
        <Link href={`/courses/${courseId}`} className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
          <ChevronLeft size={15} />
          返回课程
        </Link>

        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight text-foreground">数据面板</h1>
          <p className="mt-1 text-sm text-muted-foreground">课程问答统计与学习行为洞察</p>
        </div>

        {err && (
          <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/8 px-4 py-3 text-sm text-destructive">
            <AlertCircle size={15} />
            {err}
          </div>
        )}

        {/* Stats row */}
        {!data && !err && (
          <div className="grid grid-cols-2 gap-3">
            {Array.from({length:2}).map((_,i) => <Skeleton key={i} className="h-24 w-full rounded-xl" />)}
          </div>
        )}

        {data && (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-2">
              <div className="rounded-xl border border-border bg-card p-5 space-y-1">
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <MessageSquare size={12} />总问答数
                </div>
                <div className="text-3xl font-bold text-foreground font-ui">{data.total_questions.toLocaleString()}</div>
              </div>
              <div className="rounded-xl border border-border bg-card p-5 space-y-1">
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Clock size={12} />平均耗时
                </div>
                <div className="text-3xl font-bold text-foreground font-ui">
                  {data.avg_response_time_ms > 0 ? `${Math.round(data.avg_response_time_ms)} ms` : "—"}
                </div>
              </div>
            </div>

            {/* Top questions */}
            <section className="space-y-3">
              <h2 className="font-display text-base font-semibold text-foreground flex items-center gap-2">
                <MessageSquare size={15} className="text-primary" />
                热点问题 TOP {data.top_questions.length}
              </h2>
              {data.top_questions.length === 0 ? (
                <p className="text-sm text-muted-foreground py-4">暂无问答数据</p>
              ) : (
                <div className="space-y-2">
                  {data.top_questions.map((q, idx) => (
                    <div key={idx} className="flex items-start justify-between gap-4 rounded-xl border border-border bg-card px-4 py-3">
                      <div className="flex items-start gap-3">
                        <span className="shrink-0 flex h-5 w-5 items-center justify-center rounded-full bg-primary/10 text-primary text-[10px] font-bold mt-0.5">
                          {idx + 1}
                        </span>
                        <p className="text-sm text-foreground leading-relaxed">{q.question}</p>
                      </div>
                      <span className="shrink-0 text-xs font-semibold text-muted-foreground">{q.count} 次</span>
                    </div>
                  ))}
                </div>
              )}
            </section>

            {/* Active students */}
            <section className="space-y-3">
              <h2 className="font-display text-base font-semibold text-foreground flex items-center gap-2">
                <Users size={15} className="text-primary" />
                活跃学生
              </h2>
              {data.active_students.length === 0 ? (
                <p className="text-sm text-muted-foreground py-4">暂无学生活跃数据</p>
              ) : (
                <div className="space-y-2">
                  {data.active_students.map((s) => (
                    <div key={s.student_id} className="flex items-center justify-between rounded-xl border border-border bg-card px-4 py-3">
                      <span className="text-sm font-medium text-foreground">{s.name || s.student_id}</span>
                      <div className="text-xs text-muted-foreground text-right">
                        <span className="font-semibold text-foreground">{s.question_count}</span> 次问答
                        <br />
                        {new Date(s.last_active).toLocaleDateString("zh-CN")}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>

            {/* Top materials */}
            <section className="space-y-3">
              <h2 className="font-display text-base font-semibold text-foreground flex items-center gap-2">
                <FileText size={15} className="text-primary" />
                命中资料排行
              </h2>
              {data.top_materials.length === 0 ? (
                <p className="text-sm text-muted-foreground py-4">暂无资料命中数据</p>
              ) : (
                <div className="space-y-2">
                  {data.top_materials.map((m, idx) => (
                    <div key={m.material_id} className="flex items-center justify-between rounded-xl border border-border bg-card px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground w-4">{idx + 1}.</span>
                        <span className="text-sm font-medium text-foreground truncate max-w-xs">
                          {m.title || m.material_id}
                        </span>
                      </div>
                      <span className="text-xs font-semibold text-muted-foreground">{m.hit_count} 次命中</span>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  );
}
