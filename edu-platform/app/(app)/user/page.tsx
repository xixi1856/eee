"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { User, Mail, Shield, CheckCircle2, AlertCircle, Eye, EyeOff, KeyRound } from "lucide-react";
import { cn } from "@/lib/utils";

type UserData = {
  id: string;
  username: string;
  email: string;
  role: string;
  created_at?: string;
};

export default function UserPage() {
  const [user, setUser] = useState<UserData | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editEmail, setEditEmail] = useState("");
  const [editUsername, setEditUsername] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showCurrent, setShowCurrent] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [savingPwd, setSavingPwd] = useState(false);
  const [notification, setNotification] = useState<{ type: "success" | "error"; msg: string } | null>(null);

  function notify(type: "success" | "error", msg: string) {
    setNotification({ type, msg });
    setTimeout(() => setNotification(null), 3000);
  }

  useEffect(() => {
    void (async () => {
      setLoading(true);
      const res = await fetch("/api/v1/user", { credentials: "include" });
      if (res.ok) {
        const u = (await res.json()) as UserData;
        setUser(u);
        setEditEmail(u.email);
        setEditUsername(u.username);
      }
      setLoading(false);
    })();
  }, []);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!user) return;
    setSaving(true);
    try {
      const res = await fetch("/api/v1/user", {
        method: "PUT",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: editEmail, username: editUsername }),
      });
      if (!res.ok) {
        const j = (await res.json().catch(() => ({}))) as { error?: { message?: string } };
        notify("error", j.error?.message ?? "保存失败"); return;
      }
      const u = (await res.json()) as UserData;
      setUser(u);
      notify("success", "个人信息已更新");
    } finally {
      setSaving(false);
    }
  }

  async function handleChangePassword(e: React.FormEvent) {
    e.preventDefault();
    if (!currentPassword || !newPassword || !confirmPassword) {
      notify("error", "请填写全部密码字段");
      return;
    }
    if (newPassword !== confirmPassword) {
      notify("error", "两次输入的新密码不一致");
      return;
    }
    setSavingPwd(true);
    try {
      const res = await fetch("/api/v1/me/password", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      const data = (await res.json()) as { error?: { message?: string } };
      if (!res.ok) {
        notify("error", data.error?.message ?? "修改密码失败");
        return;
      }
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      notify("success", "密码已更新");
    } finally {
      setSavingPwd(false);
    }
  }

  const roleLabel: Record<string, string> = {
    TEACHER: "教师", STUDENT: "学生", ADMIN: "管理员",
  };

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
          <h1 className="font-display text-2xl font-semibold tracking-tight text-foreground">个人中心</h1>
          <p className="mt-1 text-sm text-muted-foreground">管理你的账户基本信息</p>
        </div>

        {loading ? (
          <div className="space-y-4">
            <Skeleton className="h-16 w-16 rounded-full" />
            <Skeleton className="h-5 w-1/2" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : user ? (
          <>
            {/* Avatar + role */}
            <div className="flex items-center gap-4">
              <Avatar className="h-14 w-14">
                <AvatarFallback className="bg-primary/15 text-primary font-semibold text-lg">
                  {user.username[0]?.toUpperCase()}
                </AvatarFallback>
              </Avatar>
              <div>
                <p className="font-semibold text-foreground">{user.username}</p>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <Shield size={12} className="text-muted-foreground" />
                  <span className="text-xs text-muted-foreground">{roleLabel[user.role] ?? user.role}</span>
                  {user.created_at ? (
                    <>
                      <span className="text-muted-foreground/40">·</span>
                      <span className="text-xs text-muted-foreground">
                        注册于 {new Date(user.created_at).toLocaleDateString("zh-CN")}
                      </span>
                    </>
                  ) : null}
                </div>
              </div>
            </div>

            {/* Edit form */}
            <form onSubmit={(e) => void handleSave(e)} className="space-y-4">
              <div className="space-y-1.5">
                <label className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                  <User size={13} />用户名
                </label>
                <Input value={editUsername} onChange={e => setEditUsername(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <label className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                  <Mail size={13} />邮箱
                </label>
                <Input type="email" value={editEmail} onChange={e => setEditEmail(e.target.value)} />
              </div>
              <Button type="submit" disabled={saving}>
                {saving ? "保存中…" : "保存修改"}
              </Button>
            </form>

            <Separator className="my-8" />

            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <KeyRound size={18} className="text-muted-foreground" />
                <h2 className="font-display text-lg font-semibold tracking-tight text-foreground">修改密码</h2>
              </div>
              <p className="text-sm text-muted-foreground">修改成功后，其他设备上的登录会话将失效。</p>
              <form onSubmit={(e) => void handleChangePassword(e)} className="space-y-4">
                <div className="space-y-1.5">
                  <label className="text-sm font-medium text-foreground" htmlFor="current-password">
                    当前密码
                  </label>
                  <div className="relative">
                    <Input
                      id="current-password"
                      type={showCurrent ? "text" : "password"}
                      autoComplete="current-password"
                      value={currentPassword}
                      onChange={(e) => setCurrentPassword(e.target.value)}
                      disabled={savingPwd}
                      className="pr-10"
                    />
                    <button
                      type="button"
                      onClick={() => setShowCurrent(!showCurrent)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                      aria-label={showCurrent ? "隐藏密码" : "显示密码"}
                    >
                      {showCurrent ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                </div>
                <div className="space-y-1.5">
                  <label className="text-sm font-medium text-foreground" htmlFor="new-password">
                    新密码
                  </label>
                  <div className="relative">
                    <Input
                      id="new-password"
                      type={showNew ? "text" : "password"}
                      autoComplete="new-password"
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      disabled={savingPwd}
                      className="pr-10"
                    />
                    <button
                      type="button"
                      onClick={() => setShowNew(!showNew)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                      aria-label={showNew ? "隐藏密码" : "显示密码"}
                    >
                      {showNew ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                </div>
                <div className="space-y-1.5">
                  <label className="text-sm font-medium text-foreground" htmlFor="confirm-password">
                    确认新密码
                  </label>
                  <div className="relative">
                    <Input
                      id="confirm-password"
                      type={showConfirm ? "text" : "password"}
                      autoComplete="new-password"
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      disabled={savingPwd}
                      className="pr-10"
                    />
                    <button
                      type="button"
                      onClick={() => setShowConfirm(!showConfirm)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                      aria-label={showConfirm ? "隐藏密码" : "显示密码"}
                    >
                      {showConfirm ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                </div>
                <Button type="submit" variant="secondary" disabled={savingPwd}>
                  {savingPwd ? "提交中…" : "更新密码"}
                </Button>
              </form>
            </div>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">加载失败，请刷新重试。</p>
        )}
      </div>
    </div>
  );
}
