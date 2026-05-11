import type { UserRole } from "@prisma/client";
import { prisma } from "@/lib/db";

/**
 * Course IDs the platform user may query via RAG (student: enrollments;
 * teacher: courses they teach). Used by internal Agent APIs.
 */
export async function listCourseIdsForRag(
  platformUserId: string,
  role: UserRole,
): Promise<string[]> {
  if (role === "TEACHER") {
    const rows = await prisma.course.findMany({
      where: { teacherId: platformUserId, isDeleted: false },
      select: { id: true },
    });
    return rows.map((r) => r.id);
  }
  if (role === "STUDENT") {
    const rows = await prisma.courseEnrollment.findMany({
      where: { studentId: platformUserId },
      select: { courseId: true },
    });
    return rows.map((r) => r.courseId);
  }
  if (role === "ADMIN") {
    const rows = await prisma.course.findMany({
      where: { isDeleted: false },
      select: { id: true },
      take: 500,
    });
    return rows.map((r) => r.id);
  }
  return [];
}
