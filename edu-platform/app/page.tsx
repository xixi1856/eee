import Link from "next/link";
import { GraduationCap, BookOpen, MessageSquare, BarChart3, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";

const features = [
  {
    icon: BookOpen,
    title: "课程知识库",
    desc: "上传 PDF、Markdown、文本文件，自动向量化索引，支持精准 RAG 检索",
  },
  {
    icon: MessageSquare,
    title: "流式 AI 问答",
    desc: "学生提问，AI 实时流式回答并标注引用来源，支持按课时筛选检索范围",
  },
  {
    icon: BarChart3,
    title: "教学数据洞察",
    desc: "热点问题 TOP 15、活跃学生排行、材料命中统计，助力教学决策",
  },
];

export default function HomePage() {
  return (
    <main className="min-h-screen bg-background text-foreground">
      {/* Nav */}
      <nav className="sticky top-0 z-40 border-b border-border bg-background/80 backdrop-blur-sm">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
          <div className="flex items-center gap-2.5">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <GraduationCap size={15} />
            </div>
            <span className="font-display text-sm font-semibold tracking-tight">EduAgent Campus</span>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" asChild>
              <Link href="/login">登录</Link>
            </Button>
            <Button size="sm" asChild>
              <Link href="/register">免费注册</Link>
            </Button>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="relative overflow-hidden">
        <div className="pointer-events-none absolute -top-40 left-1/4 h-80 w-80 rounded-full bg-primary opacity-10 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-20 right-1/4 h-64 w-64 rounded-full bg-primary opacity-8 blur-3xl" />
        <div className="relative mx-auto max-w-4xl px-6 py-20 text-center lg:py-28">
          <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-primary/25 bg-primary/8 px-3.5 py-1 text-xs font-medium text-primary">
            <span className="h-1.5 w-1.5 rounded-full bg-primary animate-pulse" />
            AI 驱动的教育平台
          </div>
          <h1 className="font-display text-4xl font-semibold leading-tight tracking-tight text-foreground sm:text-5xl lg:text-6xl">
            让教学流程与{" "}
            <span className="text-primary">AI 学习支持</span>
            {" "}自然融合
          </h1>
          <p className="mx-auto mt-6 max-w-xl text-base text-muted-foreground leading-relaxed">
            教师一键发布课程、上传知识库；学生通过 AI 助手即时获得可追溯的课程知识问答，
            数据面板洞察学习薄弱环节。
          </p>
          <div className="mt-10 flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
            <Button size="lg" className="h-11 px-8 font-semibold" asChild>
              <Link href="/register">
                开始使用
                <ArrowRight size={16} className="ml-2" />
              </Link>
            </Button>
            <Button variant="outline" size="lg" className="h-11 px-8" asChild>
              <Link href="/login">已有账号，去登录</Link>
            </Button>
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="mx-auto max-w-6xl px-6 pb-20">
        <div className="mb-10 text-center">
          <h2 className="font-display text-2xl font-semibold text-foreground sm:text-3xl">
            从课程创建到学习闭环
          </h2>
          <p className="mt-3 text-sm text-muted-foreground">
            完整的教与学工作流，一个平台全覆盖
          </p>
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          {features.map(({ icon: Icon, title, desc }) => (
            <div
              key={title}
              className="group rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/40 hover:shadow-sm"
            >
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
                <Icon size={20} />
              </div>
              <h3 className="font-display text-base font-semibold text-foreground mb-1.5">
                {title}
              </h3>
              <p className="text-sm text-muted-foreground leading-relaxed">{desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-border py-8">
        <div className="mx-auto max-w-6xl px-6 text-center text-xs text-muted-foreground">
          © 2026 EduAgent Campus · 让每次提问都有迹可循
        </div>
      </footer>
    </main>
  );
}

