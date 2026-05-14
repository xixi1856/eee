import type { UserRole } from "@prisma/client";
import { listCourseIdsForRag } from "@/lib/course-rag-courses";

/**
 * Returns the course IDs the platform user may access for RAG queries.
 * Delegates entirely to course-rag-courses.ts (student/teacher/admin logic).
 */
export async function getAccessibleCourseIds(
  userId: string,
  role: UserRole,
): Promise<string[]> {
  return listCourseIdsForRag(userId, role);
}
