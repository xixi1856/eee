import type { AssignmentStatus } from "@prisma/client";

export type { AssignmentStatus };

export type QuestionType = "single_choice" | "multi_choice" | "fill_blank" | "short_answer";
export type ObjectiveType = "knowledge" | "comprehension" | "application" | "synthesis" | "innovation";

export interface QuestionItem {
  id: number;
  type: QuestionType;
  objective: ObjectiveType;
  /** All knowledge entities this question draws on (primary first). application/synthesis/innovation may have multiple. */
  entities: string[];
  importance_score: number;
  /** Number of reasoning steps the LLM self-reported for this question. Used for difficulty_match evaluation. */
  reasoning_steps: number;
  question: string;
  options: string[];
  answer: string;
  explanation: string;
  source_chunk_ids: string[];
  /** Teacher-assigned point value (default 5) */
  score: number;
  /** Difficulty level inherited from the blueprint slot */
  difficulty?: "easy" | "medium" | "hard";
}

export interface BlueprintQuestion {
  id: number;
  type: QuestionType;
  objective: ObjectiveType;
  difficulty: "easy" | "medium" | "hard";
  entity_names: string[];
  focus: string;
}

export interface Blueprint {
  title: string;
  difficulty_weights: { easy: number; medium: number; hard: number };
  questions: BlueprintQuestion[];
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
  difficulty_distribution_score: number;
  summary: string;
}

// ── Request bodies ──────────────────────────────────────────────────────────

export interface StructuredGenerationParams {
  lessonIds: string[];
  lessonNames: string[];
  knowledgePoints: string[];
  difficultyWeights: { easy: number; medium: number; hard: number };
  count: number;
  typeWeights: Record<string, number>;
  objectiveWeights: Record<string, number>;
}

export interface GenerateAssignmentBody {
  title: string;
  teacherRequest: string;
  deadline?: string; // ISO-8601
  structuredParams?: StructuredGenerationParams;
}

export interface PatchAssignmentBody {
  title?: string;
  description?: string;
  deadline?: string; // ISO-8601 or null to clear
  questions?: QuestionItem[];
}

export interface RegenerateQuestionBody {
  entityNames: string[];
  qType: QuestionType;
  objective: ObjectiveType;
  qId: number;
  extraRequirements?: string;
  /** Current question text, used by LLM as reference when regenerating */
  currentQuestion?: string;
}

export interface CompleteQuestionBody {
  entityNames: string[];
  qType: QuestionType;
  objective: ObjectiveType;
  /** Teacher-written stem (HTML or plain text). AI will not modify this. */
  questionStem: string;
  /** Optional answer hint from the teacher. */
  answerHint?: string;
  /** Point value for the new question (default 5). */
  score?: number;
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
