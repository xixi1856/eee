"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { ChevronLeft, CheckCircle2, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";

type CourseDetail = {
  id: string;
  name: string;
  description: string | null;
  cover_image_url: string | null;
  status: string;
};

export default function EditCoursePage() {
  const params = useParams();
  const router = useRouter();
  const courseId = typeof params?.courseId === "string" ? params.courseId : null;
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [coverImageUrl, setCoverImageUrl] = useState("");
  const [notify, setNotify] = useState<{ type: "success" | "error"; msg: string } | null>(null);

  function showNotify(type: "success" | "error", msg: string) {
    setNotify({ type, msg });
    setTimeout(() => setNotify(null), 3000);
  }

  useEffect(() => {
    if (!courseId) return;
    let cancelled = false;
    void (async () => {
      setLoading(true);
      const res = await fetch(`/api/v1/courses/${courseId}`, { credentials: "include" });
      if (!cancelled && res.ok) {
        const d = (await res.json()) as { course?: CourseDetail };
        if (d.course) {
          setName(d.course.name);
          setDescription(d.course.description ?? "");
          setCoverImageUrl(d.course.cover_image_url ?? "");
        }
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [courseId]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!courseId || !name.trim()) { showNotify("error", "课程名称不能为空"); return; }
    setSaving(true);
    try {
      const res = await fetch(`/api/v1/courses/${courseId}`, {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          description: description.trim() || null,
          cover_image_url: coverImageUrl.trim() || null,
        }),
      });
      if (!res.ok) {
        const d = (await res.json().catch(() => ({}))) as { error?: { message?: string } };
        showNotify("error", d.error?.message ?? "更新失败"); return;
      }
      showNotify("success", "课程信息已更新");
      setTimeout(() => router.push(`/courses/${courseId}`), 800);
    } finally {
      setSaving(false);
    }
  }

  if (!courseId) return (
    <div className="flex items-center justify-center h-full text-muted-foreground text-sm">无效的课程链接</div>
  );

  return (
    <div className="flex flex-col h-full overflow-auto">
      {notify && (
        <div className={cn(
          "fixed top-4 left-1/2 -translate-x-1/2 z-50 flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium shadow-lg",
          notify.type === "success" ? "bg-[oklch(0.92_0.08_145)] text-[oklch(0.35_0.10_145)]" : "bg-destructive text-destructive-foreground"
        )}>
          {notify.type === "success" ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}
          {notify.msg}
        </div>
      )}
      <div className="max-w-xl mx-auto w-full px-6 py-8 space-y-6">
        <Link href={`/courses/${courseId}`} className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
          <ChevronLeft size={15} />
          返回课程详情
        </Link>
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight text-foreground">编辑课程</h1>
          <p className="mt-1 text-sm text-muted-foreground">更新课程对学生可见的基础信息。</p>
        </div>
        {loading ? (
          <div className="space-y-4">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : (
          <form onSubmit={(e) => void handleSubmit(e)} className="space-y-5">
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-foreground">课程名称 <span className="text-destructive">*</span></label>
              <Input maxLength={80} value={name} onChange={e => setName(e.target.value)} required />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-foreground">课程描述</label>
              <Textarea rows={4} maxLength={500} value={description} onChange={e => setDescription(e.target.value)} className="resize-none" />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-foreground">封面图 URL</label>
              <Input placeholder="https://..." value={coverImageUrl} onChange={e => setCoverImageUrl(e.target.value)} />
            </div>
            <div className="flex items-center gap-3">
              <Button type="submit" disabled={saving}>{saving ? "保存中…" : "保存变更"}</Button>
              <Button type="button" variant="outline" onClick={() => router.push(`/courses/${courseId}`)}>取消</Button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
