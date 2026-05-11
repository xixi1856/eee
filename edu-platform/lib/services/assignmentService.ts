import { prisma } from "@/lib/db";
import { getRedis } from "@/lib/redis";
import { ApiError } from "@/lib/http/api-error";
import { assertTeacherOfCourse, assertUuid } from "@/lib/course-access";
import { getEduAgentBaseUrl, getEduAgentApiKey } from "@/lib/config";
import { AssignmentStatus, UserRole } from "@prisma/client";
import type {
  AssignmentDetailDto,
  AssignmentSummaryDto,
  Blueprint,
  GenerateAssignmentBody,
  PatchAssignmentBody,
  QualityReport,
  QuestionItem,
  RegenerateQuestionBody,
} from "@/lib/dto/assignment.dto";

const STREAM_NAME = process.env.RAG_TASK_STREAM_NAME ?? "edu:rag:tasks:stream";

// ── Private helpers ─────────────────────────────────────────────────────────

function toSummary(a: {
  id: string;
  title: string;
  status: AssignmentStatus;
  questions: unknown;
  qualityReport: unknown;
  deadline: Date | null;
  createdAt: Date;
  errorMessage: string | null;
}): AssignmentSummaryDto {
  const qs = Array.isArray(a.questions) ? (a.questions as QuestionItem[]) : null;
  const qr = a.qualityReport ? (a.qualityReport as QualityReport) : null;
  return {
    id: a.id,
    title: a.title,
    status: a.status,
    questionCount: qs?.length ?? 0,
    qualityScore: qr?.overall_score ?? null,
    deadline: a.deadline?.toISOString() ?? null,
    createdAt: a.createdAt.toISOString(),
    errorMessage: a.errorMessage,
  };
}

function toDetail(a: {
  id: string;
  title: string;
  description: string | null;
  status: AssignmentStatus;
  questions: unknown;
  blueprint: unknown;
  qualityReport: unknown;
  deadline: Date | null;
  createdAt: Date;
  publishedAt: Date | null;
  errorMessage: string | null;
}): AssignmentDetailDto {
  const summary = toSummary(a);
  return {
    ...summary,
    description: a.description,
    blueprint: a.blueprint ? (a.blueprint as Blueprint) : null,
    questions: a.questions ? (a.questions as QuestionItem[]) : null,
    qualityReport: a.qualityReport ? (a.qualityReport as QualityReport) : null,
    publishedAt: a.publishedAt?.toISOString() ?? null,
  };
}

// ── Public service functions ─────────────────────────────────────────────────

export async function listAssignments(
  teacherId: string,
  role: UserRole,
  courseId: string,
): Promise<AssignmentSummaryDto[]> {
  await assertTeacherOfCourse(teacherId, role, courseId);
  const rows = await prisma.assignment.findMany({
    where: { courseId },
    orderBy: { createdAt: "desc" },
    select: {
      id: true,
      title: true,
      status: true,
      questions: true,
      qualityReport: true,
      deadline: true,
      createdAt: true,
      errorMessage: true,
    },
  });
  return rows.map(toSummary);
}

export async function getAssignment(
  teacherId: string,
  role: UserRole,
  courseId: string,
  assignmentId: string,
): Promise<AssignmentDetailDto> {
  await assertTeacherOfCourse(teacherId, role, courseId);
  assertUuid(assignmentId, "assignment_id");
  const a = await prisma.assignment.findFirst({
    where: { id: assignmentId, courseId },
  });
  if (!a) throw new ApiError(404, "NOT_FOUND", "Assignment not found");
  return toDetail(a);
}

export async function triggerAssignmentGeneration(
  teacherId: string,
  role: UserRole,
  courseId: string,
  body: GenerateAssignmentBody,
): Promise<AssignmentSummaryDto> {
  await assertTeacherOfCourse(teacherId, role, courseId);

  if (!body.title?.trim())
    throw new ApiError(400, "VALIDATION_ERROR", "title is required");
  if (!body.teacherRequest?.trim())
    throw new ApiError(400, "VALIDATION_ERROR", "teacherRequest is required");

  const assignment = await prisma.assignment.create({
    data: {
      courseId,
      createdBy: teacherId,
      title: body.title.trim(),
      status: AssignmentStatus.GENERATING,
      deadline: body.deadline ? new Date(body.deadline) : null,
    },
    select: {
      id: true,
      title: true,
      status: true,
      questions: true,
      qualityReport: true,
      deadline: true,
      createdAt: true,
      errorMessage: true,
    },
  });

  // Push task to Redis Stream (same stream as RAG worker)
  try {
    const redis = await getRedis();
    await redis.xAdd(STREAM_NAME, "*", {
      operation: "assignment.generate",
      assignment_id: assignment.id,
      course_id: courseId,
      teacher_request: body.teacherRequest.trim(),
    });
  } catch (err) {
    // Roll back DB record if we can't push to Redis
    await prisma.assignment.delete({ where: { id: assignment.id } }).catch(() => {});
    throw new ApiError(
      503,
      "SERVICE_UNAVAILABLE",
      "Could not queue assignment generation. Is the Redis stream running?",
    );
  }

  return toSummary(assignment);
}

export async function patchAssignment(
  teacherId: string,
  role: UserRole,
  courseId: string,
  assignmentId: string,
  body: PatchAssignmentBody,
): Promise<AssignmentDetailDto> {
  await assertTeacherOfCourse(teacherId, role, courseId);
  assertUuid(assignmentId, "assignment_id");

  const existing = await prisma.assignment.findFirst({
    where: { id: assignmentId, courseId },
  });
  if (!existing) throw new ApiError(404, "NOT_FOUND", "Assignment not found");
  if (existing.status !== AssignmentStatus.DRAFT)
    throw new ApiError(409, "CONFLICT", "Only DRAFT assignments can be edited");

  const updated = await prisma.assignment.update({
    where: { id: assignmentId },
    data: {
      ...(body.title !== undefined && { title: body.title }),
      ...(body.description !== undefined && { description: body.description }),
      ...(body.deadline !== undefined && {
        deadline: body.deadline ? new Date(body.deadline) : null,
      }),
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ...(body.questions !== undefined && { questions: body.questions as any }),
    },
  });
  return toDetail(updated);
}

export async function publishAssignment(
  teacherId: string,
  role: UserRole,
  courseId: string,
  assignmentId: string,
): Promise<AssignmentDetailDto> {
  await assertTeacherOfCourse(teacherId, role, courseId);
  assertUuid(assignmentId, "assignment_id");

  const existing = await prisma.assignment.findFirst({
    where: { id: assignmentId, courseId },
  });
  if (!existing) throw new ApiError(404, "NOT_FOUND", "Assignment not found");
  if (existing.status !== AssignmentStatus.DRAFT)
    throw new ApiError(409, "CONFLICT", "Only DRAFT assignments can be published");

  const updated = await prisma.assignment.update({
    where: { id: assignmentId },
    data: {
      status: AssignmentStatus.PUBLISHED,
      publishedAt: new Date(),
    },
  });
  return toDetail(updated);
}

export async function regenerateQuestion(
  teacherId: string,
  role: UserRole,
  courseId: string,
  assignmentId: string,
  body: RegenerateQuestionBody,
): Promise<QuestionItem> {
  const assignment = await getAssignment(teacherId, role, courseId, assignmentId);
  if (assignment.status !== AssignmentStatus.DRAFT)
    throw new ApiError(409, "CONFLICT", "Can only regenerate questions for DRAFT assignments");

  const agentBase = getEduAgentBaseUrl();
  if (!agentBase)
    throw new ApiError(503, "AGENT_UNAVAILABLE", "EDU_AGENT_BASE_URL is not configured");

  const headers = new Headers({ "Content-Type": "application/json" });
  const apiKey = getEduAgentApiKey();
  if (apiKey) headers.set("Authorization", `Bearer ${apiKey}`);

  const res = await fetch(`${agentBase}/v1/assignment/regenerate-question`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      course_id: courseId,
      entity_name: body.entityName,
      q_type: body.qType,
      objective: body.objective,
      q_id: body.qId,
      extra_requirements: body.extraRequirements ?? "",
    }),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(502, "AGENT_CHAT_FAILED", `Agent regenerate failed: ${res.status} ${text.slice(0, 200)}`);
  }

  const newQuestion = (await res.json()) as QuestionItem;

  // Update the questions array in DB atomically
  const questions = (assignment.questions ?? []) as QuestionItem[];
  const updated = questions.map((q) => (q.id === body.qId ? { ...newQuestion, score: q.score } : q));
  // If question id not found (new question), append it
  if (!updated.find((q) => q.id === body.qId)) updated.push({ ...newQuestion, score: 5 });

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  await prisma.assignment.update({
    where: { id: assignmentId },
    data: { questions: updated as any },
  });

  return newQuestion;
}
