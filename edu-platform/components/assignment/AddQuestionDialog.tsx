"use client";

import * as React from "react";
import { Loader2, PlusCircle, RefreshCw, CheckCircle2, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type {
  CompleteQuestionBody,
  ObjectiveType,
  QuestionItem,
  QuestionType,
} from "@/lib/dto/assignment.dto";

// ── Label mappings ────────────────────────────────────────────────────

const QUESTION_TYPES: { value: QuestionType; label: string }[] = [
  { value: "single_choice", label: "单选题" },
  { value: "multi_choice", label: "多选题" },
  { value: "fill_blank", label: "填空题" },
  { value: "short_answer", label: "简答题" },
];

const OBJECTIVES: { value: ObjectiveType; label: string }[] = [
  { value: "knowledge", label: "知识记忆" },
  { value: "comprehension", label: "理解分析" },
  { value: "application", label: "应用实践" },
  { value: "synthesis", label: "综合评价" },
  { value: "innovation", label: "创新拓展" },
];

// ── Types ───────────────────────────────────────────────────────────────

interface AddQuestionDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * Call AI to preview completion. Should throw (with Error.message) on failure.
   * No DB write happens — only returns the generated question.
   */
  onPreview: (body: Omit<CompleteQuestionBody, "score">) => Promise<QuestionItem>;
  /**
   * Add the finalized question to the assignment (local state only).
   * Parent is responsible for persisting via 保存草稿.
   */
  onAdd: (question: QuestionItem, score: number) => void;
}

// ── Component ──────────────────────────────────────────────────────────────

export function AddQuestionDialog({
  open,
  onOpenChange,
  onPreview,
  onAdd,
}: AddQuestionDialogProps) {
  // ── Input fields ────────────────────────────────────────────────────
  const [qType, setQType] = React.useState<QuestionType>("single_choice");
  const [objective, setObjective] = React.useState<ObjectiveType>("knowledge");
  const [entityName, setEntityName] = React.useState("");
  const [questionStem, setQuestionStem] = React.useState("");
  const [answerHint, setAnswerHint] = React.useState("");
  const [score, setScore] = React.useState(5);
  const [errors, setErrors] = React.useState<Record<string, string>>({});

  // ── AI preview state ──────────────────────────────────────────────────
  const [previewing, setPreviewing] = React.useState(false);
  const [previewError, setPreviewError] = React.useState<string | null>(null);
  const [previewBase, setPreviewBase] = React.useState<QuestionItem | null>(null);
  // Editable fields populated from AI result
  const [previewOptions, setPreviewOptions] = React.useState<string[]>([]);
  const [previewAnswer, setPreviewAnswer] = React.useState("");
  const [previewExplanation, setPreviewExplanation] = React.useState("");

  const isMCQ = qType === "single_choice" || qType === "multi_choice";

  // Reset everything when sheet opens
  React.useEffect(() => {
    if (open) {
      setQType("single_choice");
      setObjective("knowledge");
      setEntityName("");
      setQuestionStem("");
      setAnswerHint("");
      setScore(5);
      setErrors({});
      setPreviewing(false);
      setPreviewError(null);
      setPreviewBase(null);
      setPreviewOptions([]);
      setPreviewAnswer("");
      setPreviewExplanation("");
    }
  }, [open]);

  // When AI result arrives, populate editable preview fields
  React.useEffect(() => {
    if (previewBase) {
      setPreviewOptions(previewBase.options ?? []);
      setPreviewAnswer(previewBase.answer ?? "");
      setPreviewExplanation(previewBase.explanation ?? "");
    }
  }, [previewBase]);

  function clearPreview() {
    setPreviewBase(null);
    setPreviewError(null);
  }

  // ── Validation ───────────────────────────────────────────────────────
  function validate(): boolean {
    const next: Record<string, string> = {};
    if (!entityName.trim()) next.entityName = "请填写知识点";
    if (!questionStem.trim()) next.questionStem = "请填写题干";
    if (score < 1 || score > 100) next.score = "分値应在 1–100 之间";
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  // ── Handlers ──────────────────────────────────────────────────────────
  async function handlePreview() {
    if (!validate()) return;
    setPreviewing(true);
    setPreviewError(null);
    setPreviewBase(null);
    try {
      const result = await onPreview({
        qType,
        objective,
        entityName: entityName.trim(),
        questionStem: questionStem.trim(),
        answerHint: answerHint.trim(),
      });
      setPreviewBase(result);
    } catch (e) {
      setPreviewError(e instanceof Error ? e.message : "AI 补全失败，请重试");
    } finally {
      setPreviewing(false);
    }
  }

  function handleAdd() {
    if (!previewBase) return;
    const finalQuestion: QuestionItem = {
      ...previewBase,
      // Always use teacher's original stem — never the LLM's rewrite
      question: questionStem.trim(),
      options: previewOptions,
      answer: previewAnswer,
      explanation: previewExplanation,
      score,
    };
    onAdd(finalQuestion, score);
  }

  // ── Render ──────────────────────────────────────────────────────────────
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:max-w-lg overflow-y-auto">
        <SheetHeader className="mb-5">
          <SheetTitle className="flex items-center gap-2">
            <PlusCircle size={18} />
            添加自定义题目
          </SheetTitle>
          <SheetDescription>
            填写题干后点击「AI 补全」，AI 将在表单内实时填入选项与解析，可修改后再确认添加。
          </SheetDescription>
        </SheetHeader>

        <div className="space-y-5 pb-6">
          {/* 题型 */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">题型 *</label>
            <div className="flex flex-wrap gap-2">
              {QUESTION_TYPES.map(({ value, label }) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => { setQType(value); clearPreview(); }}
                  className={`px-3 py-1 rounded-full text-sm border transition-colors ${
                    qType === value
                      ? "bg-primary text-primary-foreground border-primary"
                      : "border-border hover:bg-muted"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* 认知层次 */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">认知层次 *</label>
            <div className="flex flex-wrap gap-2">
              {OBJECTIVES.map(({ value, label }) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setObjective(value)}
                  className={`px-3 py-1 rounded-full text-sm border transition-colors ${
                    objective === value
                      ? "bg-primary text-primary-foreground border-primary"
                      : "border-border hover:bg-muted"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* 知识点 */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">知识点 *</label>
            <Input
              placeholder="例如：TCP 三次握手"
              value={entityName}
              onChange={(e) => { setEntityName(e.target.value); clearPreview(); }}
              disabled={previewing}
            />
            {errors.entityName && (
              <p className="text-xs text-destructive">{errors.entityName}</p>
            )}
          </div>

          {/* 题干 */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">题干 *</label>
            <p className="text-xs text-muted-foreground">此内容 AI 不会修改，原样保留。</p>
            <Textarea
              placeholder="输入完整题目题干..."
              rows={5}
              value={questionStem}
              onChange={(e) => { setQuestionStem(e.target.value); clearPreview(); }}
              disabled={previewing}
              className="resize-none"
            />
            {errors.questionStem && (
              <p className="text-xs text-destructive">{errors.questionStem}</p>
            )}
          </div>

          {/* 答案提示 */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">
              答案提示 <span className="font-normal text-muted-foreground">（可选）</span>
            </label>
            <Textarea
              placeholder="告诉 AI 正确答案方向，例如：答案是B，原因是..."
              rows={2}
              value={answerHint}
              onChange={(e) => setAnswerHint(e.target.value)}
              disabled={previewing}
              className="resize-none"
            />
          </div>

          {/* AI 补全按钮 */}
          <Button
            type="button"
            variant={previewBase ? "outline" : "default"}
            className="w-full gap-2"
            onClick={() => void handlePreview()}
            disabled={previewing || !questionStem.trim() || !entityName.trim()}
          >
            {previewing ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                AI 补全中，请稍候...
              </>
            ) : previewBase ? (
              <>
                <RefreshCw size={14} />
                重新补全
              </>
            ) : (
              <>✨ AI 补全选项与解析</>
            )}
          </Button>

          {/* 错误提示 */}
          {previewError && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
              <AlertCircle size={14} className="shrink-0 mt-0.5" />
              {previewError}
            </div>
          )}

          {/* AI 补全结果（可编辑） */}
          {previewBase && (
            <div className="rounded-lg border bg-muted/30 p-4 space-y-4">
              <div className="flex items-center gap-1.5 text-sm font-medium text-green-700">
                <CheckCircle2 size={14} />
                AI 已补全，可在下方直接修改
              </div>

              {/* 选项（仅选择题） */}
              {isMCQ && (
                <div className="space-y-2">
                  <p className="text-xs font-medium text-muted-foreground">选项</p>
                  {(previewOptions.length ? previewOptions : ["A. ", "B. ", "C. ", "D. "]).map(
                    (opt, i) => (
                      <Input
                        key={i}
                        value={opt}
                        onChange={(e) => {
                          const next = [...previewOptions];
                          next[i] = e.target.value;
                          setPreviewOptions(next);
                        }}
                        className="text-sm"
                      />
                    ),
                  )}
                </div>
              )}

              {/* 答案 */}
              <div className="space-y-1.5">
                <p className="text-xs font-medium text-muted-foreground">答案</p>
                <Input
                  value={previewAnswer}
                  onChange={(e) => setPreviewAnswer(e.target.value)}
                  className="text-sm"
                />
              </div>

              {/* 解析 */}
              <div className="space-y-1.5">
                <p className="text-xs font-medium text-muted-foreground">解析</p>
                <Textarea
                  value={previewExplanation}
                  onChange={(e) => setPreviewExplanation(e.target.value)}
                  rows={3}
                  className="text-sm resize-none"
                />
              </div>
            </div>
          )}

          {/* 分値 */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">分値</label>
            <Input
              type="number"
              min={1}
              max={100}
              value={score}
              onChange={(e) => setScore(Number(e.target.value))}
              className="w-24"
            />
            {errors.score && (
              <p className="text-xs text-destructive">{errors.score}</p>
            )}
          </div>
        </div>

        {/* Footer — sticky at bottom */}
        <div className="sticky bottom-0 bg-background pt-4 pb-2 border-t flex justify-end gap-2">
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={previewing}
          >
            取消
          </Button>
          <Button
            onClick={handleAdd}
            disabled={previewing || !previewBase}
            title={!previewBase ? "请先点击「AI 补全」" : undefined}
          >
            确认添加
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
