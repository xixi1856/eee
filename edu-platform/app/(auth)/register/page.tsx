"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Eye, EyeOff, Loader2, Copy, Check, KeyRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type Role = "STUDENT" | "TEACHER";

export default function RegisterPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("STUDENT");
  const [showPass, setShowPass] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [credCode, setCredCode] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!username.trim() || !email.trim() || !password) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/v1/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.trim(), email: email.trim(), password, role }),
      });
      const data = (await res.json()) as {
        credential?: { code?: string };
        error?: { message?: string };
      };
      if (!res.ok) throw new Error(data.error?.message ?? "注册失败");
      if (data.credential?.code) {
        setCredCode(data.credential.code);
      } else {
        router.push("/login");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "注册失败，请重试");
    } finally {
      setLoading(false);
    }
  }

  async function handleCopy() {
    if (!credCode) return;
    await navigator.clipboard.writeText(credCode);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  // Credential code dialog overlay
  if (credCode) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
        <div className="mx-4 w-full max-w-md rounded-2xl bg-card border border-border p-8 shadow-2xl">
          <div className="flex items-center gap-3 mb-5">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/15 text-primary">
              <KeyRound size={20} />
            </div>
            <div>
              <h3 className="font-display text-lg font-semibold text-foreground">注册成功</h3>
              <p className="text-xs text-muted-foreground">请妥善保存你的 Agent 绑定凭证码</p>
            </div>
          </div>

          <div className="rounded-xl border border-border bg-muted/50 p-4 mb-5">
            <p className="text-xs text-muted-foreground mb-2">绑定凭证码（仅显示一次）</p>
            <div className="flex items-center justify-between gap-3">
              <code className="text-2xl font-mono font-bold tracking-[0.25em] text-foreground">
                {credCode}
              </code>
              <button
                onClick={() => void handleCopy()}
                className="flex h-8 w-8 items-center justify-center rounded-lg border border-border bg-background text-muted-foreground hover:text-foreground hover:border-primary transition-all"
              >
                {copied ? <Check size={14} className="text-green-600" /> : <Copy size={14} />}
              </button>
            </div>
          </div>

          <p className="text-xs text-muted-foreground mb-5 leading-relaxed">
            此凭证码用于将 EduAgent 助手与你的账号绑定，
            <strong className="text-foreground"> 仅在本消息中显示一次</strong>，
            请立即复制并妥善保存。绑定后你将可以使用 AI 课堂问答功能。
          </p>

          <Button
            className="w-full"
            onClick={() => router.push("/login")}
          >
            已保存，去登录
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Title */}
      <div className="space-y-1">
        <h2 className="font-display text-2xl font-semibold tracking-tight text-foreground">
          创建账号
        </h2>
        <p className="text-sm text-muted-foreground">
          注册后即可加入课程并使用 AI 学习助手。
        </p>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/8 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Form */}
      <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4">
        <div className="space-y-1.5">
          <label className="text-sm font-medium text-foreground" htmlFor="username">
            用户名
          </label>
          <Input
            id="username"
            type="text"
            autoComplete="username"
            placeholder="设置你的用户名"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            disabled={loading}
            required
            className="h-10 bg-background"
          />
        </div>

        <div className="space-y-1.5">
          <label className="text-sm font-medium text-foreground" htmlFor="email">
            邮箱
          </label>
          <Input
            id="email"
            type="email"
            autoComplete="email"
            placeholder="your@email.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={loading}
            required
            className="h-10 bg-background"
          />
        </div>

        <div className="space-y-1.5">
          <label className="text-sm font-medium text-foreground" htmlFor="password">
            密码
          </label>
          <div className="relative">
            <Input
              id="password"
              type={showPass ? "text" : "password"}
              autoComplete="new-password"
              placeholder="至少 8 位，含大小写+数字"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={loading}
              required
              minLength={8}
              className="h-10 bg-background pr-10"
            />
            <button
              type="button"
              onClick={() => setShowPass(!showPass)}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
            >
              {showPass ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </div>

        <div className="space-y-1.5">
          <label className="text-sm font-medium text-foreground" htmlFor="role">
            角色
          </label>
          <div className="grid grid-cols-2 gap-2">
            {(["STUDENT", "TEACHER"] as Role[]).map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => setRole(r)}
                className={cn(
                  "h-10 rounded-lg border text-sm font-medium transition-all",
                  role === r
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-background text-muted-foreground hover:border-primary/50 hover:text-foreground"
                )}
              >
                {r === "STUDENT" ? "学生" : "教师"}
              </button>
            ))}
          </div>
        </div>

        <Button
          type="submit"
          disabled={loading || !username.trim() || !email.trim() || !password}
          className={cn("w-full h-10 font-semibold mt-1", loading && "cursor-not-allowed")}
        >
          {loading ? (
            <>
              <Loader2 size={16} className="mr-2 animate-spin" />
              注册中…
            </>
          ) : (
            "创建账号"
          )}
        </Button>
      </form>

      <p className="text-center text-sm text-muted-foreground">
        已有账号？{" "}
        <Link
          href="/login"
          className="font-medium text-primary underline-offset-4 hover:underline"
        >
          立即登录
        </Link>
      </p>
    </div>
  );
}
