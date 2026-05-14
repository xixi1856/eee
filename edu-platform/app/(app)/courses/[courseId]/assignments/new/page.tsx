"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  ChevronLeft,
  Sparkles,
  Settings2,
  AlertCircle,
  CheckCircle2,
  Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";
import type { StructuredGenerationParams } from "@/lib/dto/assignment.dto";

type Mode = "nlp" | "structured";

const MIN_SEG_PCT = 10;
const DIFFICULTY_STEP = 5;

function clampDifficultySplits(s1: number, s2: number): [number, number] {
  let a = Math.round(s1 / DIFFICULTY_STEP) * DIFFICULTY_STEP;
  let b = Math.round(s2 / DIFFICULTY_STEP) * DIFFICULTY_STEP;
  a = Math.max(MIN_SEG_PCT, Math.min(a, 100 - 2 * MIN_SEG_PCT));
  b = Math.max(a + MIN_SEG_PCT, Math.min(b, 100 - MIN_SEG_PCT));
  return [a, b];
}

function DifficultyRangeSlider({
  value,
  onChange,
}: {
  value: { easy: number; medium: number; hard: number };
  onChange: (v: { easy: number; medium: number; hard: number }) => void;
}) {
  const rawS1 = Math.round(value.easy * 100);
  const rawS2 = Math.round((value.easy + value.medium) * 100);
  const [split1, split2] = clampDifficultySplits(rawS1, rawS2);
  const easyPct = split1;
  const mediumPct = split2 - split1;
  const hardPct = 100 - split2;

  const trackGradient = `linear-gradient(to right, rgb(34 197 94) 0%, rgb(34 197 94) ${split1}%, rgb(234 179 8) ${split1}%, rgb(234 179 8) ${split2}%, rgb(239 68 68) ${split2}%, rgb(239 68 68) 100%)`;

  return (
    <div className="space-y-3">
      <div className="flex justify-between text-xs font-medium">
        <span className="text-green-600 dark:text-green-400">简单 {easyPct}%</span>
        <span className="text-yellow-600 dark:text-yellow-400">中等 {mediumPct}%</span>
        <span className="text-red-500 dark:text-red-400">困难 {hardPct}%</span>
      </div>
      <div className="relative py-1">
        <div
          className="pointer-events-none absolute left-0 right-0 top-1/2 h-2 -translate-y-1/2 rounded-full"
          style={{ background: trackGradient }}
          aria-hidden
        />
        <Slider
          min={0}
          max={100}
          step={DIFFICULTY_STEP}
          minStepsBetweenThumbs={MIN_SEG_PCT / DIFFICULTY_STEP}
          value={[split1, split2]}
          onValueChange={(vals) => {
            const [s1, s2] = vals;
            const [a, b] = clampDifficultySplits(s1, s2);
            onChange({
              easy: a / 100,
              medium: (b - a) / 100,
              hard: (100 - b) / 100,
            });
          }}
          trackClassName="bg-transparent"
          rangeClassName="opacity-0"
          className="relative z-10"
          aria-label="难度分布：调整简单、中等、困难占比"
        />
      </div>
    </div>
  );
}

const QUESTION_TYPES = [
  { key: "single_choice", label: "单选题" },
  { key: "multi_choice", label: "多选题" },
  { key: "fill_blank", label: "填空题" },
  { key: "short_answer", label: "简答题" },
] as const;

const OBJECTIVES = [
  { key: "knowledge", label: "记忆" },
  { key: "comprehension", label: "理解" },
  { key: "application", label: "应用" },
  { key: "synthesis", label: "综合" },
  { key: "innovation", label: "创新" },
] as const;

type Lesson = { id: string; title: string; order_index: number };

export default function NewAssignmentPage() {
  const { courseId } = useParams<{ courseId: string }>();
  const router = useRouter();

  // ── Shared state ──────────────────────────────────────────────────────────
  const [mode, setMode] = useState<Mode>("nlp");
  const [title, setTitle] = useState("");
  const [deadline, setDeadline] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ── NLP mode ──────────────────────────────────────────────────────────────
  const [teacherRequest, setTeacherRequest] = useState("");

  // ── Structured mode ───────────────────────────────────────────────────────
  const [lessons, setLessons] = useState<Lesson[]>([]);
  const [selectedLessons, setSelectedLessons] = useState<string[]>([]);
  const [knowledgeTags, setKnowledgeTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const [difficultyWeights, setDifficultyWeights] = useState({ easy: 0.2, medium: 0.6, hard: 0.2 });
  const [count, setCount] = useState(20);
  const [countInput, setCountInput] = useState("20");
  const [selectedTypes, setSelectedTypes] = useState<string[]>(["single_choice", "fill_blank", "short_answer"]);
  const [selectedObjectives, setSelectedObjectives] = useState<string[]>(["knowledge", "comprehension", "application"]);

  useEffect(() => {
    void fetch(`/api/v1/courses/${courseId}/lessons`, { credentials: "include" })
      .then((r) => r.json())
      .then((d: { lessons?: Lesson[] }) =>
        setLessons((d.lessons ?? []).sort((a, b) => a.order_index - b.order_index))
      );
  }, [courseId]);

  // ── Helpers ───────────────────────────────────────────────────────────────
  function toggleLesson(id: string) {
    setSelectedLessons((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  }
  function toggleType(key: string) {
    setSelectedTypes((prev) =>
      prev.includes(key) ? (prev.length > 1 ? prev.filter((x) => x !== key) : prev) : [...prev, key]
    );
  }
  function toggleObjective(key: string) {
    setSelectedObjectives((prev) =>
      prev.includes(key) ? (prev.length > 1 ? prev.filter((x) => x !== key) : prev) : [...prev, key]
    );
  }
  function addTag(raw: string) {
    const tags = raw.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
    setKnowledgeTags((prev) => {
      const next = [...prev];
      tags.forEach((t) => { if (!next.includes(t)) next.push(t); });
      return next;
    });
    setTagInput("");
  }

  function clampQuestionCount(n: number) {
    return Math.min(50, Math.max(5, Math.round(n)));
  }
  function commitQuestionCount(next: number) {
    const c = clampQuestionCount(next);
    setCount(c);
    setCountInput(String(c));
  }

  // ── Submit ────────────────────────────────────────────────────────────────
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!title.trim()) { setError("请填写作业标题"); return; }

    let finalRequest = teacherRequest.trim();
    let structuredParams: StructuredGenerationParams | undefined;

    if (mode === "nlp") {
      if (finalRequest.length < 10) {
        setError("需求描述至少需要 10 个字符");
        return;
      }
    } else {
      if (selectedTypes.length === 0 || selectedObjectives.length === 0) {
        setError("请至少选择一种题型和一个认知目标");
        return;
      }
      // Normalise weights: equal split among selected
      const typeWeights: Record<string, number> = {};
      selectedTypes.forEach((k) => { typeWeights[k] = 1 / selectedTypes.length; });
      const objectiveWeights: Record<string, number> = {};
      selectedObjectives.forEach((k) => { objectiveWeights[k] = 1 / selectedObjectives.length; });

      const lessonNames = selectedLessons
        .map((id) => lessons.find((l) => l.id === id)?.title ?? "")
        .filter(Boolean);

      structuredParams = {
        lessonIds: selectedLessons,
        lessonNames,
        knowledgePoints: knowledgeTags,
        difficultyWeights,
        count,
        typeWeights,
        objectiveWeights,
      };

      // Auto-generate a request string if not provided
      if (!finalRequest) {
        const parts: string[] = [];
        if (lessonNames.length) parts.push(`课时：${lessonNames.join("、")}`);
        if (knowledgeTags.length) parts.push(`知识点：${knowledgeTags.join("、")}`);
        const ep = Math.round(difficultyWeights.easy * 100);
        const mp = Math.round(difficultyWeights.medium * 100);
        const hp = Math.round(difficultyWeights.hard * 100);
        parts.push(`难度分布：简单${ep}%/中等${mp}%/困难${hp}%`);
        parts.push(`共 ${count} 道题`);
        finalRequest = parts.join("；");
      }
    }

    setSubmitting(true);
    try {
      const res = await fetch(`/api/v1/courses/${courseId}/assignments`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: title.trim(),
          teacherRequest: finalRequest,
          deadline: deadline || undefined,
          structuredParams,
        }),
      });
      if (!res.ok) {
        const d = (await res.json()) as { message?: string };
        setError(d.message ?? "提交失败，请重试");
        return;
      }
      const d = (await res.json()) as { assignment?: { id: string } };
      const aid = d.assignment?.id;
      if (aid) {
        router.push(`/courses/${courseId}/assignments/${aid}`);
      } else {
        router.push(`/courses/${courseId}?tab=assignments`);
      }
    } catch {
      setError("网络错误，请重试");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="h-full overflow-auto">
      <div className="max-w-4xl mx-auto w-full px-6 py-8 space-y-6">
      {/* Back */}
      <Link
        href={`/courses/${courseId}`}
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
      >
        <ChevronLeft size={15} />
        返回课程
      </Link>

      <div className="space-y-1">
        <h1 className="text-xl font-semibold">新建作业</h1>
        <p className="text-sm text-muted-foreground">AI 将根据课程知识库自动生成题目，生成后可编辑</p>
      </div>

      <form onSubmit={(e) => void handleSubmit(e)} className="space-y-6">
        {/* Mode switcher */}
        <div className="flex gap-2 p-1 rounded-xl bg-muted w-fit">
          <button
            type="button"
            onClick={() => setMode("nlp")}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors",
              mode === "nlp"
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            <Sparkles size={13} />
            自然语言
          </button>
          <button
            type="button"
            onClick={() => setMode("structured")}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors",
              mode === "structured"
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            <Settings2 size={13} />
            结构化参数
          </button>
        </div>

        {/* Shared: title + deadline */}
        <div className="space-y-4 rounded-xl border border-border bg-card p-5">
          <h2 className="text-sm font-semibold text-foreground">基本信息</h2>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">作业标题 *</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="例：第三章 运输层 综合练习"
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-colors"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">截止时间（选填）</label>
            <input
              type="datetime-local"
              value={deadline}
              onChange={(e) => setDeadline(e.target.value)}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-colors"
            />
          </div>
        </div>

        {/* NLP mode */}
        {mode === "nlp" && (
          <div className="space-y-4 rounded-xl border border-border bg-card p-5">
            <h2 className="text-sm font-semibold text-foreground">需求描述</h2>
            <textarea
              value={teacherRequest}
              onChange={(e) => setTeacherRequest(e.target.value)}
              rows={5}
              placeholder="描述你的作业需求，例如：针对运输层TCP协议，生成15道中等难度题目，侧重三次握手和流量控制，包含单选题和简答题。"
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground resize-none focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-colors"
            />
            <p className="text-xs text-muted-foreground">至少 10 个字符</p>
          </div>
        )}

        {/* Structured mode */}
        {mode === "structured" && (
          <div className="space-y-5">
            {/* Lessons */}
            {lessons.length > 0 && (
              <div className="rounded-xl border border-border bg-card p-5 space-y-3">
                <h2 className="text-sm font-semibold text-foreground">选择课时（可多选，空 = 全课程）</h2>
                <div className="flex flex-wrap gap-2">
                  {lessons.map((l) => (
                    <button
                      key={l.id}
                      type="button"
                      onClick={() => toggleLesson(l.id)}
                      className={cn(
                        "px-3 py-1 rounded-full text-xs font-medium border transition-colors",
                        selectedLessons.includes(l.id)
                          ? "bg-primary text-primary-foreground border-primary"
                          : "border-border text-muted-foreground hover:border-primary hover:text-foreground"
                      )}
                    >
                      {l.order_index + 1}. {l.title}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Knowledge points */}
            <div className="rounded-xl border border-border bg-card p-5 space-y-3">
              <h2 className="text-sm font-semibold text-foreground">知识点标签（选填）</h2>
              <div className="flex flex-wrap gap-2 min-h-[28px]">
                {knowledgeTags.map((tag) => (
                  <span
                    key={tag}
                    className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-primary/10 text-primary text-xs font-medium"
                  >
                    {tag}
                    <button
                      type="button"
                      onClick={() => setKnowledgeTags((p) => p.filter((t) => t !== tag))}
                      className="hover:text-destructive"
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
              <input
                type="text"
                value={tagInput}
                onChange={(e) => setTagInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") { e.preventDefault(); addTag(tagInput); }
                  if (e.key === ",") { e.preventDefault(); addTag(tagInput); }
                }}
                onBlur={() => tagInput.trim() && addTag(tagInput)}
                placeholder="输入知识点后按 Enter 或逗号添加"
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-colors"
              />
            </div>

            {/* Count + difficulty */}
            <div className="grid grid-cols-2 gap-4">
              <div className="rounded-xl border border-border bg-card p-5 space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-sm font-semibold text-foreground">题目数量</h2>
                  <div className="flex items-center gap-1.5">
                    <label htmlFor="structured-count" className="text-xs text-muted-foreground whitespace-nowrap">
                      精确值
                    </label>
                    <input
                      id="structured-count"
                      type="text"
                      inputMode="numeric"
                      autoComplete="off"
                      value={countInput}
                      onChange={(e) => setCountInput(e.target.value.replace(/[^\d]/g, ""))}
                      onBlur={() => {
                        const v = parseInt(countInput, 10);
                        if (Number.isNaN(v) || countInput === "") {
                          setCountInput(String(count));
                          return;
                        }
                        commitQuestionCount(v);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          (e.target as HTMLInputElement).blur();
                        }
                      }}
                      className="w-14 rounded-md border border-border bg-background px-2 py-1 text-center text-sm font-semibold text-primary tabular-nums focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
                    />
                    <span className="text-xs text-muted-foreground">题（5–50）</span>
                  </div>
                </div>
                <input
                  type="range"
                  min={5}
                  max={50}
                  step={1}
                  value={count}
                  onChange={(e) => commitQuestionCount(Number(e.target.value))}
                  className="w-full accent-primary"
                />
                <div className="flex justify-between text-xs text-muted-foreground">
                  <span>5</span>
                  <span>50</span>
                </div>
              </div>

              <div className="rounded-xl border border-border bg-card p-5 space-y-3">
                <h2 className="text-sm font-semibold text-foreground">难度分布</h2>
                <DifficultyRangeSlider value={difficultyWeights} onChange={setDifficultyWeights} />
              </div>
            </div>

            {/* Type weights */}
            <div className="rounded-xl border border-border bg-card p-5 space-y-3">
              <h2 className="text-sm font-semibold text-foreground">
                题型分布
                <span className="ml-2 text-xs font-normal text-muted-foreground">（勾选 = 均等分配）</span>
              </h2>
              <div className="flex flex-wrap gap-2">
                {QUESTION_TYPES.map(({ key, label }) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => toggleType(key)}
                    className={cn(
                      "px-3 py-1 rounded-full text-xs font-medium border transition-colors",
                      selectedTypes.includes(key)
                        ? "bg-primary text-primary-foreground border-primary"
                        : "border-border text-muted-foreground hover:border-primary hover:text-foreground"
                    )}
                  >
                    {label}
                    {selectedTypes.includes(key) && (
                      <span className="ml-1 opacity-70">
                        {Math.round((1 / selectedTypes.length) * 100)}%
                      </span>
                    )}
                  </button>
                ))}
              </div>
            </div>

            {/* Objective weights */}
            <div className="rounded-xl border border-border bg-card p-5 space-y-3">
              <h2 className="text-sm font-semibold text-foreground">
                认知目标
                <span className="ml-2 text-xs font-normal text-muted-foreground">（勾选 = 均等分配）</span>
              </h2>
              <div className="flex flex-wrap gap-2">
                {OBJECTIVES.map(({ key, label }) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => toggleObjective(key)}
                    className={cn(
                      "px-3 py-1 rounded-full text-xs font-medium border transition-colors",
                      selectedObjectives.includes(key)
                        ? "bg-primary text-primary-foreground border-primary"
                        : "border-border text-muted-foreground hover:border-primary hover:text-foreground"
                    )}
                  >
                    {label}
                    {selectedObjectives.includes(key) && (
                      <span className="ml-1 opacity-70">
                        {Math.round((1 / selectedObjectives.length) * 100)}%
                      </span>
                    )}
                  </button>
                ))}
              </div>
            </div>

            {/* Optional extra request */}
            <div className="rounded-xl border border-border bg-card p-5 space-y-3">
              <h2 className="text-sm font-semibold text-foreground">补充说明（选填）</h2>
              <textarea
                value={teacherRequest}
                onChange={(e) => setTeacherRequest(e.target.value)}
                rows={3}
                placeholder="如有额外要求可在此说明，留空则自动生成需求描述"
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground resize-none focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-colors"
              />
            </div>
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            <AlertCircle size={14} className="shrink-0" />
            {error}
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center justify-end gap-3">
          <Button type="button" variant="outline" size="sm" asChild>
            <Link href={`/courses/${courseId}`}>取消</Link>
          </Button>
          <Button type="submit" size="sm" disabled={submitting} className="gap-1.5">
            {submitting ? (
              <><Loader2 size={13} className="animate-spin" />提交中…</>
            ) : (
              <><CheckCircle2 size={13} />开始生成</>
            )}
          </Button>
        </div>
      </form>
      </div>
    </div>
  );
}
