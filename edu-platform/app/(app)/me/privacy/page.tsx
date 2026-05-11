"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Shield, Download, Trash2, CheckCircle2, AlertCircle, AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";

type Me = {
  id: string;
  qa_collection_enabled?: boolean;
  qa_collection_notice_accepted_at?: string | null;
};

type QaExport = {
  user_id: string;
  qa_logs: unknown[];
};

function useNotify() {
  const [n, setN] = useState<{ type: "success" | "error"; msg: string } | null>(null);
  const notify = (type: "success" | "error", msg: string) => { setN({ type, msg }); setTimeout(() => setN(null), 4000); };
  return { notification: n, notify };
}

export default function PrivacyPage() {
  const [user, setUser] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const { notification, notify } = useNotify();

  async function loadUser() {
    setLoading(true);
    try {
      const res = await fetch("/api/v1/user", { credentials: "include" });
      if (res.ok) setUser((await res.json()) as Me);
      else notify("error", "加载用户信息失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void loadUser(); }, []);

  async function updateCollection(enabled: boolean) {
    setSaving(true);
    try {
      const res = await fetch("/api/v1/user", {
        method: "PUT", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ qa_collection_enabled: enabled }),
      });
      if (res.ok) {
        setUser(prev => prev ? { ...prev, qa_collection_enabled: enabled } : prev);
        notify("success", enabled ? "已开启学习数据采集" : "已关闭学习数据采集");
      } else {
        notify("error", "更新失败");
      }
    } finally {
      setSaving(false);
    }
  }

  async function exportQaLogs() {
    setSaving(true);
    try {
      const res = await fetch("/api/v1/me/qa-logs/export", { credentials: "include" });
      if (!res.ok) { notify("error", "导出失败"); return; }
      const body = (await res.json()) as QaExport;
      const blob = new Blob([JSON.stringify(body, null, 2)], { type: "application/json;charset=utf-8" });
      const href = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = href; a.download = `qa-logs-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      URL.revokeObjectURL(href);
      notify("success", `导出成功，共 ${(body.qa_logs ?? []).length} 条记录`);
    } finally {
      setSaving(false);
    }
  }

  async function deleteQaLogs() {
    if (!window.confirm("确定删除所有问答记录？此操作不可撤销。")) return;
    setSaving(true);
    try {
      const res = await fetch("/api/v1/me/qa-logs", { method: "DELETE", credentials: "include" });
      if (res.ok) {
        const b = (await res.json()) as { deleted_count?: number };
        notify("success", `删除完成，共处理 ${b.deleted_count ?? 0} 条记录`);
      } else {
        notify("error", "删除失败");
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col h-full overflow-auto">
      {notification && (
        <div className={cn(
          "fixed top-4 left-1/2 -translate-x-1/2 z-50 flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium shadow-lg",
          notification.type === "success" ? "bg-[oklch(0.92_0.08_145)] text-[oklch(0.35_0.10_145)]" : "bg-destructive text-destructive-foreground"
        )}>
          {notification.type === "success" ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}
          {notification.msg}
        </div>
      )}

      <div className="max-w-xl mx-auto w-full px-6 py-8 space-y-8">
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight text-foreground flex items-center gap-2">
            <Shield size={20} className="text-primary" />隐私与数据
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">管理问答数据采集、导出与删除（GDPR）</p>
        </div>

        {loading ? (
          <div className="space-y-4">
            <Skeleton className="h-20 w-full rounded-xl" />
            <Skeleton className="h-20 w-full rounded-xl" />
          </div>
        ) : (
          <>
            {/* Data collection toggle */}
            <div className="rounded-xl border border-border bg-card p-5 space-y-4">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-sm font-semibold text-foreground">问答数据采集</p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    关闭后仍可继续聊天，但不会写入 QA 日志用于统计分析。
                  </p>
                </div>
                <button
                  onClick={() => void updateCollection(!user?.qa_collection_enabled)}
                  disabled={saving}
                  className={cn(
                    "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    user?.qa_collection_enabled ? "bg-primary" : "bg-muted-foreground/30"
                  )}
                >
                  <span className={cn(
                    "inline-block h-4 w-4 rounded-full bg-white shadow transition-transform",
                    user?.qa_collection_enabled ? "translate-x-6" : "translate-x-1"
                  )} />
                </button>
              </div>
              {user?.qa_collection_notice_accepted_at ? (
                <div className="flex items-center gap-2 text-xs text-[oklch(0.45_0.10_145)]">
                  <CheckCircle2 size={12} />
                  已接受采集说明：{new Date(user.qa_collection_notice_accepted_at).toLocaleDateString("zh-CN")}
                </div>
              ) : (
                <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800/40 px-3 py-2.5">
                  <AlertTriangle size={13} className="text-amber-500 shrink-0 mt-0.5" />
                  <p className="text-xs text-amber-700 dark:text-amber-300">
                    你尚未确认数据采集说明，可在首次聊天时确认或在此关闭采集。
                  </p>
                </div>
              )}
            </div>

            {/* Export / delete */}
            <div className="rounded-xl border border-border bg-card p-5 space-y-4">
              <div>
                <p className="text-sm font-semibold text-foreground">数据可携带与擦除</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  你可以导出自己的问答记录，也可以执行软删除操作清空历史数据。
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" onClick={() => void exportQaLogs()} disabled={saving}>
                  <Download size={13} className="mr-1.5" />导出我的 QA 记录
                </Button>
                <Button variant="outline" size="sm" onClick={() => void deleteQaLogs()} disabled={saving}
                  className="text-destructive border-destructive/30 hover:bg-destructive/8 hover:text-destructive">
                  <Trash2 size={13} className="mr-1.5" />删除我的 QA 记录
                </Button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
