"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ChevronLeft, CheckCircle2, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";

export default function CreateCoursePage() {
  const router = useRouter();
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [coverImageUrl, setCoverImageUrl] = useState("");
  const [notify, setNotify] = useState<{ type: "success" | "error"; msg: string } | null>(null);

  function showNotify(type: "success" | "error", msg: string) {
    setNotify({ type, msg });
    setTimeout(() => setNotify(null), 3000);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) { showNotify("error", "请填写课程名称"); return; }
    setSaving(true);
    try {
      const res = await fetch("/api/v1/courses", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          name: name.trim(),
          description: description.trim() || undefined,
          cover_image_url: coverImageUrl.trim() || undefined,
        }),
      });
      if (!res.ok) {
        const j = (await res.json().catch(() => ({}))) as { message?: string };
        showNotify("error", j.message ?? `创建失败 (${res.status})`);
        return;
      }
      const j = (await res.json()) as { course?: { id: string } };
      showNotify("success", "课程创建成功！");
      setTimeout(() => router.push(`/courses/${j.course?.id ?? ""}`), 800);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col h-full overflow-auto">
      {/* Notification */}
      {notify && (
        <div className={cn(
          "fixed top-4 left-1/2 -translate-x-1/2 z-50 flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium shadow-lg",
          notify.type === "success"
            ? "bg-[oklch(0.92_0.08_145)] text-[oklch(0.35_0.10_145)]"
            : "bg-destructive text-destructive-foreground"
        )}>
          {notify.type === "success" ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}
          {notify.msg}
        </div>
      )}

      <div className="max-w-xl mx-auto w-full px-6 py-8 space-y-6">
        <Link href="/courses" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
          <ChevronLeft size={15} />
          返回课程列表
        </Link>

        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight text-foreground">创建新课程</h1>
          <p className="mt-1 text-sm text-muted-foreground">填写课程基本信息，创建后可继续上传教学材料。</p>
        </div>

        <form onSubmit={(e) => void handleSubmit(e)} className="space-y-5">
          <div className="space-y-1.5">
            <label htmlFor="name" className="text-sm font-medium text-foreground">
              课程名称 <span className="text-destructive">*</span>
            </label>
            <Input
              id="name"
              placeholder="例：计算机网络基础"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="desc" className="text-sm font-medium text-foreground">
              课程简介
            </label>
            <Textarea
              id="desc"
              placeholder="简要描述本课程的学习目标、适合人群等（选填）"
              rows={4}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="resize-none"
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="cover" className="text-sm font-medium text-foreground">
              封面图片 URL
            </label>
            <Input
              id="cover"
              placeholder="https://... （选填）"
              value={coverImageUrl}
              onChange={(e) => setCoverImageUrl(e.target.value)}
            />
          </div>

          <div className="flex items-center gap-3 pt-1">
            <Button type="submit" disabled={saving} className="w-full sm:w-auto">
              {saving ? "创建中…" : "创建课程"}
            </Button>
            <Button type="button" variant="outline" asChild>
              <Link href="/courses">取消</Link>
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
