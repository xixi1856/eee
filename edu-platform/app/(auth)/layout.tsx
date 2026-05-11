"use client";

import { GraduationCap, BookMarked, BarChart3, MessageSquare } from "lucide-react";

const features = [
  { icon: BookMarked, title: "结构化课程管理", desc: "创建课程、课时，上传多格式学习资料" },
  { icon: MessageSquare, title: "AI 课堂问答", desc: "基于课程知识库的流式问答，实时引用来源" },
  { icon: BarChart3, title: "学习数据洞察", desc: "热点问题、活跃学生与资料命中分析" },
];

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col lg:flex-row bg-background">
      {/* Left panel */}
      <aside className="hidden lg:flex lg:w-[480px] xl:w-[540px] relative flex-col justify-between p-12 overflow-hidden bg-[oklch(0.20_0.010_262)] text-white shrink-0">
        {/* Decorative blobs */}
        <div className="pointer-events-none absolute -top-32 -left-32 h-80 w-80 rounded-full bg-[oklch(0.54_0.130_68)] opacity-20 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-24 -right-16 h-64 w-64 rounded-full bg-[oklch(0.54_0.130_68)] opacity-15 blur-3xl" />
        <div className="pointer-events-none absolute top-1/2 left-1/3 h-40 w-40 rounded-full bg-[oklch(0.60_0.12_250)] opacity-10 blur-2xl" />

        {/* Brand */}
        <div className="relative z-10 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[oklch(0.54_0.130_68)] shadow-lg">
            <GraduationCap size={22} />
          </div>
          <div className="leading-tight">
            <div className="font-display text-lg font-semibold tracking-tight">EduAgent</div>
            <div className="text-[11px] font-medium tracking-[0.12em] uppercase text-white/50">Campus</div>
          </div>
        </div>

        {/* Headline */}
        <div className="relative z-10 space-y-4">
          <h1 className="font-display text-3xl font-semibold leading-tight text-white">
            让教学流程与{" "}
            <span className="text-[oklch(0.82_0.10_72)]">AI 学习支持</span>
            {" "}自然融合
          </h1>
          <p className="text-sm leading-relaxed text-white/60 max-w-xs">
            面向课程运营、课堂问答与学习追踪的教育平台。
            教师可快速创建课程，学生可获得可追溯的学习反馈。
          </p>
        </div>

        {/* Feature list */}
        <div className="relative z-10 space-y-4">
          {features.map(({ icon: Icon, title, desc }) => (
            <div key={title} className="flex items-start gap-3">
              <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-white/10">
                <Icon size={14} className="text-[oklch(0.82_0.10_72)]" />
              </div>
              <div>
                <div className="text-[13px] font-semibold text-white/90">{title}</div>
                <div className="text-[12px] text-white/50">{desc}</div>
              </div>
            </div>
          ))}
        </div>
      </aside>

      {/* Right panel */}
      <main className="flex flex-1 items-center justify-center p-6 sm:p-10">
        <div className="w-full max-w-md">
          {/* Mobile brand */}
          <div className="mb-8 flex items-center gap-2 lg:hidden">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <GraduationCap size={16} />
            </div>
            <span className="font-display text-base font-semibold">EduAgent Campus</span>
          </div>
          {children}
        </div>
      </main>
    </div>
  );
}

