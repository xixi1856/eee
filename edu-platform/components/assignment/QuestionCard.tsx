"use client";

import { useState } from "react";
import { GripVertical, RefreshCw, ChevronDown, ChevronUp, Star, Trash2 } from "lucide-react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { TiptapEditor } from "./TiptapEditor";
import type { QuestionItem } from "@/lib/dto/assignment.dto";

const Q_TYPE_LABELS: Record<string, string> = {
  MCQ: "单选题",
  MULTI_MCQ: "多选题",
  TRUE_FALSE: "判断题",
  SHORT_ANSWER: "简答题",
  ESSAY: "论述题",
};

interface QuestionCardProps {
  question: QuestionItem;
  index: number;
  onUpdate: (id: number, updates: Partial<QuestionItem>) => void;
  onDelete: (id: number) => void;
  onRegenerate: (id: number) => Promise<void>;
  regenerating?: boolean;
}

export function QuestionCard({
  question,
  index,
  onUpdate,
  onDelete,
  onRegenerate,
  regenerating = false,
}: QuestionCardProps) {
  const [expanded, setExpanded] = useState(true);

  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: String(question.id) });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  const options: string[] = Array.isArray(question.options) ? question.options : [];

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(
        "rounded-lg border bg-card shadow-sm transition-shadow",
        isDragging && "opacity-50 shadow-lg",
      )}
    >
      {/* Card header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b bg-muted/30 rounded-t-lg">
        <button
          className="cursor-grab active:cursor-grabbing text-muted-foreground hover:text-foreground touch-none"
          {...attributes}
          {...listeners}
          aria-label="拖动排序"
        >
          <GripVertical size={16} />
        </button>

        <span className="text-sm font-semibold text-muted-foreground select-none">
          Q{index + 1}
        </span>

        <span className="rounded-full bg-primary/10 text-primary px-2 py-0.5 text-xs font-medium">
          {Q_TYPE_LABELS[question.type] ?? question.type}
        </span>

        {question.score !== undefined && (
          <span className="flex items-center gap-1 text-xs text-muted-foreground ml-1">
            <Star size={11} />
            {question.score} 分
          </span>
        )}

        <div className="flex-1" />

        <Button
          size="sm"
          variant="ghost"
          className="h-7 gap-1 text-xs"
          disabled={regenerating}
          onClick={() => void onRegenerate(question.id)}
          // id is number; parent handles the numeric id
        >
          <RefreshCw size={13} className={cn(regenerating && "animate-spin")} />
          AI 重新生成
        </Button>

        <Button
          size="sm"
          variant="ghost"
          className="h-7 text-destructive hover:text-destructive"
          onClick={() => onDelete(question.id as number)}
        >
          <Trash2 size={13} />
        </Button>

        <button
          className="text-muted-foreground hover:text-foreground"
          onClick={() => setExpanded((e) => !e)}
          aria-label={expanded ? "折叠" : "展开"}
        >
          {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
        </button>
      </div>

      {/* Card body */}
      {expanded && (
        <div className="p-4 space-y-3">
          {/* Question stem */}
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1 block">题目</label>
            <TiptapEditor
              value={question.question}
              onChange={(html) => onUpdate(question.id, { question: html })}
              placeholder="题目内容…"
            />
          </div>

          {/* Options for MCQ / MULTI_MCQ / TRUE_FALSE */}
          {options.length > 0 && (
            <div>
              <label className="text-xs font-medium text-muted-foreground mb-1 block">选项</label>
              <div className="space-y-1.5">
                {options.map((opt, i) => (
                  <div key={i} className="flex items-start gap-2">
                    <span className="mt-2 text-xs font-bold text-muted-foreground w-5 shrink-0">
                      {String.fromCharCode(65 + i)}.
                    </span>
                    <input
                      className="flex-1 rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                      value={opt}
                      onChange={(e) => {
                        const updated = [...options];
                        updated[i] = e.target.value;
                        onUpdate(question.id, { options: updated } as Partial<QuestionItem>);
                      }}
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Answer */}
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1 block">参考答案</label>
            <TiptapEditor
              value={typeof question.answer === "string" ? question.answer : JSON.stringify(question.answer)}
              onChange={(html) => onUpdate(question.id, { answer: html })}
              placeholder="参考答案…"
              minHeight="60px"
            />
          </div>

          {/* Explanation */}
          {question.explanation !== undefined && (
            <div>
              <label className="text-xs font-medium text-muted-foreground mb-1 block">解析</label>
              <TiptapEditor
                value={question.explanation ?? ""}
                onChange={(html) => onUpdate(question.id, { explanation: html })}
                placeholder="解析说明（选填）…"
                minHeight="60px"
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
