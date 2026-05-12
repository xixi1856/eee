import type { Course } from "@prisma/client";
import { UserRole } from "@prisma/client";
import { prisma } from "@/lib/db";
import { ApiError } from "@/lib/http/api-error";

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function isUuid(id: string): boolean {
  return UUID_RE.test(id);
}

export function assertUuid(id: string, label = "id"): void {
  if (!isUuid(id)) {
    throw new ApiError(400, "VALIDATION_ERROR", `Invalid ${label}`);
  }
}

export async function isCourseCollaborator(
  courseId: string,
  teacherId: string,
): Promise<boolean> {
  const row = await prisma.courseCollaborator.findUnique({
    where: {
      courseId_teacherId: { courseId, teacherId },
    },
  });
  return Boolean(row);
}

/** Course owner (primary teacher) only. */
export async function assertCourseOwner(
  userId: string,
  role: UserRole,
  courseId: string,
): Promise<Course> {
  assertUuid(courseId, "course_id");
  if (role !== UserRole.TEACHER) {
    throw new ApiError(403, "FORBIDDEN", "Teacher role required");
  }
  const course = await prisma.course.findFirst({
    where: { id: courseId, isDeleted: false },
  });
  if (!course) {
    throw new ApiError(404, "NOT_FOUND", "Course not found");
  }
  if (course.teacherId !== userId) {
    throw new ApiError(403, "FORBIDDEN", "Not the course owner");
  }
  return course;
}

/** Course owner or collaborator (write-capable teacher). */
export async function assertTeacherOfCourse(
  userId: string,
  role: UserRole,
  courseId: string,
): Promise<Course> {
  assertUuid(courseId, "course_id");
  if (role !== UserRole.TEACHER) {
    throw new ApiError(403, "FORBIDDEN", "Teacher role required");
  }
  const course = await prisma.course.findFirst({
    where: { id: courseId, isDeleted: false },
  });
  if (!course) {
    throw new ApiError(404, "NOT_FOUND", "Course not found");
  }
  if (course.teacherId === userId) {
    return course;
  }
  if (await isCourseCollaborator(courseId, userId)) {
    return course;
  }
  throw new ApiError(403, "FORBIDDEN", "Not a teacher for this course");
}

/** Teacher (owner or collaborator), or enrolled student (active course, not deleted). */
export async function getCourseIfMember(
  userId: string,
  role: UserRole,
  courseId: string,
): Promise<Course> {
  assertUuid(courseId, "course_id");
  const course = await prisma.course.findFirst({
    where: { id: courseId, isDeleted: false },
  });
  if (!course) {
    throw new ApiError(404, "NOT_FOUND", "Course not found");
  }
  if (role === UserRole.TEACHER) {
    if (course.teacherId === userId) return course;
    if (await isCourseCollaborator(courseId, userId)) return course;
  }
  if (role === UserRole.STUDENT) {
    const en = await prisma.courseEnrollment.findUnique({
      where: {
        courseId_studentId: { courseId, studentId: userId },
      },
    });
    if (en) return course;
  }
  throw new ApiError(403, "FORBIDDEN", "No access to this course");
}

/** For internal Agent checks: owner, collaborator, or enrolled student. */
export async function hasCourseRagAccess(
  userId: string,
  courseId: string,
): Promise<boolean> {
  assertUuid(userId, "user_id");
  assertUuid(courseId, "course_id");
  const course = await prisma.course.findFirst({
    where: { id: courseId, isDeleted: false },
  });
  if (!course) return false;
  if (course.teacherId === userId) return true;
  if (await isCourseCollaborator(courseId, userId)) return true;
  const en = await prisma.courseEnrollment.findUnique({
    where: {
      courseId_studentId: { courseId, studentId: userId },
    },
  });
  return Boolean(en);
}
