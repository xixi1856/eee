"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { AlertCircle, CheckCircle2, Key, RefreshCw, Copy, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";

type Me = { role: string };

type CredRow = {
  id: string;
  user_id: string;
  status: string;
  created_at: string;
  expires_at: string | null;
  used_at: string | null;
  bound_at: string | null;
  bound_agent_user_id: string | null;
};

const STATUS_MAP: Record<string, { label: string; cls: string }> = {
  ACTIVE: { label: "有效", cls: "status-published" },
  USED: { label: "已使用", cls: "status-processing" },
  EXPIRED: { label: "已过期", cls: "status-archived" },
  REVOKED: { label: "已撤销", cls: "status-failed" },
};

function CredStatusBadge({ status }: { status: string }) {
  const s = STATUS_MAP[status] ?? { label: status, cls: "status-archived" };
  return <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold", s.cls)}>{s.label}</span>;
}

function useNotify() {
  const [n, setN] = useState<{ type: "success" | "error"; msg: string } | null>(null);
  const notify = (type: "success" | "error", msg: string) => {
    setN({ type, msg });
    setTimeout(() => setN(null), 4000);
  };
  return { notification: n, notify };
}

export default function CredentialsPage() {
  const [role, setRole] = useState<string | null>(null);
  const [mine, setMine] = useState<CredRow[]>([]);
  const [adminRows, setAdminRows] = useState<CredRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [genUserId, setGenUserId] = useState("");
  const [genExpiry, setGenExpiry] = useState("");
  const [filterUserId, setFilterUserId] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [generatedCode, setGeneratedCode] = useState<string | null>(null);
  const [regenCode, setRegenCode] = useState<string | null>(null);
  const [regenExpiry, setRegenExpiry] = useState<string | null>(null);
  const [showRegenConfirm, setShowRegenConfirm] = useState(false);
  const [regenLoading, setRegenLoading] = useState(false);
  const { notification, notify } = useNotify();

  const loadMe = useCallback(async () => {
    const res = await fetch("/api/v1/user", { credentials: "include" });
    if (!res.ok) return;
    const u = (await res.json()) as Me;
    setRole(u.role);
  }, []);

  const loadMine = useCallback(async () => {
    const res = await fetch("/api/v1/credentials", { credentials: "include" });
    const data = (await res.json()) as { credentials?: CredRow[] };
    if (res.ok) setMine(data.credentials ?? []);
  }, []);

  const loadAdmin = useCallback(async (filters?: { user_id?: string; status?: string }) => {
    const q = new URLSearchParams();
    if (filters?.user_id) q.set("user_id", filters.user_id);
    if (filters?.status) q.set("status", filters.status);
    const res = await fetch(`/api/v1/admin/credentials?${q}`, { credentials: "include" });
    const data = (await res.json()) as { credentials?: CredRow[] };
    if (res.ok) setAdminRows(data.credentials ?? []);
  }, []);

  useEffect(() => {
    setLoading(true);
    void loadMe().finally(() => setLoading(false));
  }, [loadMe]);

  useEffect(() => {
    if (!role) return;
    if (role === "STUDENT" || role === "TEACHER") void loadMine();
    if (role === "ADMIN") void loadAdmin();
  }, [role, loadMine, loadAdmin]);

  async function generateAdmin(e: React.FormEvent) {
    e.preventDefault();
    if (!genUserId.trim()) { notify("error", "请输入目标用户 ID"); return; }
    setLoading(true);
    try {
      const res = await fetch("/api/v1/admin/credentials", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          user_id: genUserId.trim(),
          expires_in_minutes: genExpiry ? Number(genExpiry) : undefined,
        }),
      });
      if (!res.ok) {
        const d = (await res.json().catch(() => ({}))) as { error?: { message?: string } };
        notify("error", d.error?.message ?? "生成失败"); return;
      }
      const d = (await res.json()) as { code?: string };
      setGeneratedCode(d.code ?? null);
      setGenUserId(""); setGenExpiry("");
      notify("success", "凭证已生成（请复制下方凭证码，仅显示一次）");
      void loadAdmin();
    } finally {
      setLoading(false);
    }
  }

  async function revoke(id: string) {
    if (!window.confirm("确定撤销该凭证？")) return;
    const res = await fetch(`/api/v1/admin/credentials/${id}`, {
      method: "DELETE", credentials: "include",
    });
    if (res.ok) { notify("success", "已撤销"); void loadAdmin(); }
    else notify("error", "撤销失败");
  }

  async function handleRegen() {
    setRegenLoading(true);
    try {
      const res = await fetch("/api/v1/credentials/regenerate", {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) {
        const d = (await res.json().catch(() => ({}))) as { error?: { message?: string } };
        notify("error", d.error?.message ?? "重新生成失败");
        return;
      }
      const d = (await res.json()) as { code?: string; expires_at?: string };
      setRegenCode(d.code ?? null);
      setRegenExpiry(d.expires_at ?? null);
      setShowRegenConfirm(false);
      void loadMine();
    } finally {
      setRegenLoading(false);
    }
  }

  if (loading) return (
    <div className="max-w-3xl mx-auto px-6 py-8 space-y-4">
      {Array.from({length:3}).map((_,i) => <Skeleton key={i} className="h-12 w-full rounded-xl" />)}
    </div>
  );

  if (!role) return null;

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

      <div className="max-w-3xl mx-auto w-full px-6 py-8 space-y-8">
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight text-foreground flex items-center gap-2">
            <Key size={20} className="text-primary" />凭证管理
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {role === "STUDENT" || role === "TEACHER"
              ? "查看你的凭证状态与有效期"
              : "管理员：为用户生成和管理凭证"}
          </p>
        </div>

        {/* Student: view own credentials */}
        {(role === "STUDENT" || role === "TEACHER") && (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">
              凭证码在注册时由平台一次性发放，用于将 EduAgent 与你的账号绑定。
            </p>

            {/* Newly regenerated code — shown once */}
            {regenCode && (
              <div className="flex items-center gap-3 rounded-xl border border-primary/30 bg-primary/8 px-4 py-4">
                <Key size={16} className="text-primary shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium text-muted-foreground mb-1">新凭证码（仅显示一次，请立即复制）</p>
                  <p className="font-mono text-lg font-bold text-primary tracking-widest">{regenCode}</p>
                  {regenExpiry && (
                    <p className="text-xs text-muted-foreground mt-1">
                      有效至 {new Date(regenExpiry).toLocaleString("zh-CN")}
                    </p>
                  )}
                </div>
                <button
                  onClick={() => { void navigator.clipboard.writeText(regenCode); notify("success", "已复制"); }}
                  className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                >
                  <Copy size={14} />
                </button>
              </div>
            )}

            {mine.length === 0 && !regenCode ? (
              <div className="flex flex-col items-center justify-center py-16 rounded-xl border border-dashed border-border">
                <Key size={24} className="text-muted-foreground mb-2" />
                <p className="text-sm text-muted-foreground">暂无凭证记录</p>
              </div>
            ) : (
              <div className="space-y-2">
                {mine.map((c) => (
                  <div key={c.id} className="flex items-center justify-between rounded-xl border border-border bg-card px-4 py-3 gap-3">
                    <div className="min-w-0">
                      <p className="text-xs font-mono text-muted-foreground truncate">{c.id}</p>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        创建：{new Date(c.created_at).toLocaleDateString("zh-CN")}
                        {c.expires_at ? ` · 过期：${new Date(c.expires_at).toLocaleDateString("zh-CN")}` : ""}
                      </p>
                    </div>
                    <CredStatusBadge status={c.status} />
                  </div>
                ))}
              </div>
            )}

            {/* Regenerate button */}
            {!regenCode && (
              <div className="pt-1">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setShowRegenConfirm(true)}
                  disabled={regenLoading}
                >
                  <RefreshCw size={13} className="mr-1.5" />重新生成凭证
                </Button>
                <p className="mt-1.5 text-xs text-muted-foreground">
                  如果错过了凭证码，可以重新生成（旧凭证将立即失效）。
                </p>
              </div>
            )}
          </div>
        )}

        {/* Regenerate confirmation modal */}
        {showRegenConfirm && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
            <div className="mx-4 w-full max-w-sm rounded-2xl bg-card border border-border p-6 shadow-2xl space-y-4">
              <div className="flex items-start gap-3">
                <AlertCircle size={20} className="text-amber-500 shrink-0 mt-0.5" />
                <div>
                  <h3 className="font-semibold text-foreground text-sm">确认重新生成凭证？</h3>
                  <p className="text-xs text-muted-foreground mt-1">
                    当前所有有效凭证将立即失效。新凭证码仅显示一次，请立即复制保存。
                  </p>
                </div>
              </div>
              <div className="flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={() => setShowRegenConfirm(false)} disabled={regenLoading}>
                  取消
                </Button>
                <Button size="sm" onClick={() => void handleRegen()} disabled={regenLoading}>
                  {regenLoading ? "生成中…" : "确认生成"}
                </Button>
              </div>
            </div>
          </div>
        )}

        {/* Admin: generate + list */}
        {role === "ADMIN" && (
          <>
            {/* Generated code display */}
            {generatedCode && (
              <div className="flex items-center gap-3 rounded-xl border border-primary/30 bg-primary/8 px-4 py-4">
                <Key size={16} className="text-primary shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium text-muted-foreground mb-1">生成的凭证码（仅显示一次）</p>
                  <p className="font-mono text-lg font-bold text-primary tracking-widest">{generatedCode}</p>
                </div>
                <button
                  onClick={() => { void navigator.clipboard.writeText(generatedCode); notify("success", "已复制"); }}
                  className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                >
                  <Copy size={14} />
                </button>
              </div>
            )}

            {/* Generate form */}
            <div className="rounded-xl border border-border bg-card p-5 space-y-4">
              <h2 className="font-display text-base font-semibold text-foreground">为用户生成凭证</h2>
              <form onSubmit={(e) => void generateAdmin(e)} className="space-y-3">
                <div className="space-y-1.5">
                  <label className="text-sm font-medium text-foreground">目标用户 UUID <span className="text-destructive">*</span></label>
                  <Input placeholder="platform user id" value={genUserId} onChange={e => setGenUserId(e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <label className="text-sm font-medium text-foreground">有效分钟数（可选）</label>
                  <Input type="number" placeholder="留空为默认" min={1} max={525600} value={genExpiry} onChange={e => setGenExpiry(e.target.value)} className="w-40" />
                </div>
                <Button type="submit" disabled={loading}>代用户生成</Button>
              </form>
            </div>

            {/* Filter + list */}
            <div className="space-y-4">
              <div className="flex items-center gap-2 flex-wrap">
                <Input
                  placeholder="筛选 user_id"
                  value={filterUserId}
                  onChange={e => setFilterUserId(e.target.value)}
                  className="w-52"
                />
                <select
                  value={filterStatus}
                  onChange={e => setFilterStatus(e.target.value)}
                  className="h-9 rounded-md border border-input bg-background px-3 text-sm text-foreground"
                >
                  <option value="">全部状态</option>
                  {["ACTIVE", "USED", "EXPIRED", "REVOKED"].map(s => (
                    <option key={s} value={s}>{STATUS_MAP[s]?.label ?? s}</option>
                  ))}
                </select>
                <Button variant="outline" size="sm" onClick={() => void loadAdmin({ user_id: filterUserId, status: filterStatus })}>
                  <RefreshCw size={13} className="mr-1.5" />查询
                </Button>
              </div>

              {adminRows.length === 0 ? (
                <p className="text-sm text-muted-foreground py-8 text-center">暂无凭证记录</p>
              ) : (
                <div className="space-y-2">
                  {adminRows.map((c) => (
                    <div key={c.id} className="flex items-center justify-between rounded-xl border border-border bg-card px-4 py-3 gap-3">
                      <div className="min-w-0 flex-1">
                        <p className="text-xs font-mono text-muted-foreground truncate">{c.id}</p>
                        <p className="text-xs text-muted-foreground mt-0.5">
                          用户：{c.user_id} · {new Date(c.created_at).toLocaleDateString("zh-CN")}
                          {c.expires_at ? ` → ${new Date(c.expires_at).toLocaleDateString("zh-CN")}` : ""}
                        </p>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <CredStatusBadge status={c.status} />
                        {c.status === "ACTIVE" && (
                          <button
                            onClick={() => void revoke(c.id)}
                            className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors"
                          >
                            <Trash2 size={13} />
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
