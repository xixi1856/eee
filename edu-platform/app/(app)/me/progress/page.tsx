"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ChevronLeft, TrendingUp, Target, BookOpenCheck, MessageSquare, BarChart3, AlertCircle } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";

type Progress = {
  student_id: string;
  total_questions: number;
  topics_covered: string[];
  weak_areas: string[];
  recent_activity: string | null;
  engagement_score: number;
};

export default function MyProgressPage() {
  const [userId, setUserId] = useState<string | null>(null);
  const [p, setP] = useState<Progress | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void (async () => {
      setLoading(true);
      const uRes = await fetch("/api/v1/user", { credentials: "include" });
      if (!uRes.ok) { setErr("请先登录"); setLoading(false); return; }
      const u = (await uRes.json()) as { id: string };
      setUserId(u.id);
      const pr = await fetch(`/api/v1/students/${u.id}/learning-progress`, { credentials: "include" });
      if (!pr.ok) { setErr("无法加载学习进度（可能权限不足）"); setLoading(false); return; }
      setP((await pr.json()) as Progress);
      setLoading(false);
    })();
  }, []);

  return (
    <div className="flex flex-col h-full overflow-auto">
      <div className="max-w-3xl mx-auto w-full px-6 py-8 space-y-8">
        <Link href="/courses" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
          <ChevronLeft size={15} />
          课程列表
        </Link>

        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight text-foreground flex items-center gap-2">
            <TrendingUp size={20} className="text-primary" />我的学习进度
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">基于问答行为的学习轨迹分析</p>
        </div>

        {err && (
          <div className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/8 px-4 py-3 text-sm text-destructive">
            <AlertCircle size={15} />
            {err}
          </div>
        )}

        {loading && !err && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <Skeleton className="h-24 rounded-xl" />
              <Skeleton className="h-24 rounded-xl" />
            </div>
            <Skeleton className="h-32 rounded-xl" />
          </div>
        )}

        {p && (
          <>
            {/* Stats */}
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-xl border border-border bg-card p-5 space-y-1">
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <MessageSquare size={12} />累计提问
                </div>
                <div className="text-3xl font-bold text-foreground font-ui">{p.total_questions.toLocaleString()}</div>
              </div>
              <div className="rounded-xl border border-border bg-card p-5 space-y-1">
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <BarChart3 size={12} />参与度（启发式）
                </div>
                <div className="text-3xl font-bold text-foreground font-ui">{p.engagement_score.toFixed(1)}</div>
              </div>
            </div>

            {/* Recent activity */}
            {p.recent_activity && (
              <div className="rounded-xl border border-border bg-card px-4 py-3 text-sm text-muted-foreground">
                最近活动：{p.recent_activity}
              </div>
            )}

            {/* Topics covered */}
            <section className="space-y-3">
              <h2 className="font-display text-base font-semibold text-foreground flex items-center gap-2">
                <BookOpenCheck size={15} className="text-primary" />主题覆盖
              </h2>
              {p.topics_covered.length === 0 ? (
                <p className="text-sm text-muted-foreground py-4">暂无记录</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {p.topics_covered.map((t, i) => (
                    <span key={i} className="rounded-full border border-border bg-card px-3 py-1 text-xs font-medium text-foreground">
                      {t}
                    </span>
                  ))}
                </div>
              )}
            </section>

            {/* Weak areas */}
            <section className="space-y-3">
              <h2 className="font-display text-base font-semibold text-foreground flex items-center gap-2">
                <Target size={15} className="text-destructive" />薄弱环节
              </h2>
              {p.weak_areas.length === 0 ? (
                <p className="text-sm text-muted-foreground py-4">暂无弱点记录</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {p.weak_areas.map((t, i) => (
                    <span key={i} className="rounded-full border border-destructive/30 bg-destructive/8 px-3 py-1 text-xs font-medium text-destructive">
                      {t}
                    </span>
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
