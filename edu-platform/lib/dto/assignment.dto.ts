import type { AssignmentStatus } from "@prisma/client";

export type { AssignmentStatus };

export type QuestionType = "single_choice" | "multi_choice" | "fill_blank" | "short_answer";
export type ObjectiveType = "knowledge" | "comprehension" | "application" | "synthesis" | "innovation";

export interface QuestionItem {
  id: number;
  type: QuestionType;
  objective: ObjectiveType;
  entity: string;
  importance_score: number;
  question: string;
  options: string[];
  answer: string;
  explanation: string;
  source_chunk_ids: string[];
  /** Teacher-assigned point value (default 5) */
  score: number;
}

export interface Blueprint {
  title: string;
  topic_hint: string;
  difficulty: "easy" | "medium" | "hard";
  count: number;
  type_weights: Record<string, number>;
  objective_weights: Record<string, number>;
  estimated_minutes: number;
}

export interface QuestionReview {
  id: number;
  clarity: number;
  difficulty_match: number;
  issues: string[];
  suggestion: string | null;
}

export interface QualityReport {
  overall_score: number;
  passed: boolean;
  threshold: number;
  question_reviews: QuestionReview[];
  failed_ids: number[];
  summary: string;
}

// ── Request bodies ──────────────────────────────────────────────────────────

export interface GenerateAssignmentBody {
  title: string;
  teacherRequest: string;
  deadline?: string; // ISO-8601
}

export interface PatchAssignmentBody {
  title?: string;
  description?: string;
  deadline?: string; // ISO-8601 or null to clear
  questions?: QuestionItem[];
}

export interface RegenerateQuestionBody {
  entityName: string;
  qType: QuestionType;
  objective: ObjectiveType;
  qId: number;
  extraRequirements?: string;
}

// ── Response DTOs ───────────────────────────────────────────────────────────

export interface AssignmentSummaryDto {
  id: string;
  title: string;
  status: AssignmentStatus;
  questionCount: number;
  qualityScore: number | null;
  deadline: string | null;
  createdAt: string;
  errorMessage: string | null;
}

export interface AssignmentDetailDto extends AssignmentSummaryDto {
  description: string | null;
  blueprint: Blueprint | null;
  questions: QuestionItem[] | null;
  qualityReport: QualityReport | null;
  publishedAt: string | null;
}
