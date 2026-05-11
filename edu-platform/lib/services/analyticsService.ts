import { prisma } from "@/lib/db";

export type CourseAnalyticsResult = {
  total_questions: number;
  avg_response_time_ms: number;
  top_questions: {
    question: string;
    count: number;
    avg_quality: number | null;
  }[];
  active_students: {
    student_id: string;
    name: string | null;
    question_count: number;
    last_active: string;
  }[];
  top_materials: {
    material_id: string;
    title: string | null;
    hit_count: number;
  }[];
  weak_concepts: { concept: string; count: number; resources: string[] }[];
};

function parseDate(s: string | null, fallback: Date): Date {
  if (!s) return fallback;
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? fallback : d;
}

/**
 * Course-level aggregates for teachers (B3). No per-student raw Q/A in this payload.
 */
export async function getCourseAnalytics(
  courseId: string,
  startDate: string | null,
  endDate: string | null,
): Promise<CourseAnalyticsResult> {
  const end = parseDate(endDate, new Date());
  const start = parseDate(
    startDate,
    new Date(end.getTime() - 7 * 24 * 3600 * 1000),
  );

  const totals = await prisma.$queryRaw<
    { c: bigint; avg_ms: number | null }[]
  >`
    SELECT COUNT(*)::bigint AS c, AVG(execution_time_ms)::float AS avg_ms
    FROM qa_logs
    WHERE course_id = ${courseId}::uuid
      AND deleted_at IS NULL
      AND created_at >= ${start}
      AND created_at <= ${end}
  `;
  const total_questions = Number(totals[0]?.c ?? 0);
  const avg_response_time_ms = Math.round(totals[0]?.avg_ms ?? 0);

  const top_questions = await prisma.$queryRaw<
    { question: string; count: number; avg_quality: number | null }[]
  >`
    SELECT question,
           COUNT(*)::int AS count,
           AVG(response_quality)::float AS avg_quality
    FROM qa_logs
    WHERE course_id = ${courseId}::uuid
      AND deleted_at IS NULL
      AND created_at >= ${start}
      AND created_at <= ${end}
    GROUP BY question
    ORDER BY count DESC
    LIMIT 15
  `;

  const active_students = await prisma.$queryRaw<
    {
      student_id: string;
      question_count: number;
      last_active: Date;
      name: string | null;
    }[]
  >`
    SELECT l.student_id::text AS student_id,
           COUNT(*)::int AS question_count,
           MAX(l.created_at) AS last_active,
           u.real_name AS name
    FROM qa_logs l
    JOIN users u ON u.id = l.student_id
    WHERE l.course_id = ${courseId}::uuid
      AND l.deleted_at IS NULL
      AND l.created_at >= ${start}
      AND l.created_at <= ${end}
    GROUP BY l.student_id, u.real_name
    ORDER BY question_count DESC
    LIMIT 20
  `;

  const matHits = await prisma.$queryRaw<
    { material_id: string; hit_count: bigint }[]
  >`
    SELECT m AS material_id, COUNT(*)::bigint AS hit_count
    FROM qa_logs, unnest(hit_materials) AS m
    WHERE course_id = ${courseId}::uuid
      AND deleted_at IS NULL
      AND created_at >= ${start}
      AND created_at <= ${end}
    GROUP BY m
    ORDER BY hit_count DESC
    LIMIT 15
  `;

  const titles: Record<string, string | null> = {};
  for (const row of matHits) {
    const mat = await prisma.material.findFirst({
      where: { id: row.material_id, isDeleted: false },
      select: { originalFilename: true },
    });
    titles[row.material_id] = mat?.originalFilename ?? null;
  }

  return {
    total_questions,
    avg_response_time_ms,
    top_questions: top_questions.map((r) => ({
      question: r.question,
      count: r.count,
      avg_quality: r.avg_quality,
    })),
    active_students: active_students.map((r) => ({
      student_id: r.student_id,
      name: r.name,
      question_count: r.question_count,
      last_active: r.last_active.toISOString(),
    })),
    top_materials: matHits.map((r) => ({
      material_id: r.material_id,
      title: titles[r.material_id] ?? null,
      hit_count: Number(r.hit_count),
    })),
    weak_concepts: [],
  };
}

export type LearningProgressResult = {
  student_id: string;
  total_questions: number;
  topics_covered: string[];
  weak_areas: string[];
  recent_activity: string | null;
  engagement_score: number;
};

/** Heuristic progress from ``qa_logs`` (B3). */
export async function getStudentLearningProgress(
  studentId: string,
): Promise<LearningProgressResult> {
  const logs = await prisma.qaLog.findMany({
    where: { studentId, deletedAt: null },
    orderBy: { createdAt: "desc" },
    take: 500,
    select: { question: true, createdAt: true, responseQuality: true },
  });
  const total = logs.length;
  const topics = new Set<string>();
  const weak: string[] = [];
  for (const l of logs) {
    const q = l.question.trim().slice(0, 80);
    if (q.length >= 4) topics.add(q);
    if (l.responseQuality !== null && l.responseQuality <= 2) {
      weak.push(q);
    }
  }
  const recent = logs[0]?.createdAt ?? null;
  const engagement_score =
    total === 0 ? 0 : Math.min(1, total / 50 + (topics.size / 20) * 0.5);

  return {
    student_id: studentId,
    total_questions: total,
    topics_covered: [...topics].slice(0, 30),
    weak_areas: weak.slice(0, 15),
    recent_activity: recent ? recent.toISOString() : null,
    engagement_score: Math.round(engagement_score * 100) / 100,
  };
}
