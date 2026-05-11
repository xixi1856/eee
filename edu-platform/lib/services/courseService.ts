import {
  CourseStatus,
  UserRole,
  type Course,
  type Lesson,
} from "@prisma/client";
import { prisma } from "@/lib/db";
import { ApiError } from "@/lib/http/api-error";
import { assertUuid, getCourseIfMember, assertTeacherOfCourse } from "@/lib/course-access";
import type {
  CourseSummaryDto,
  CreateCourseBody,
  CreateLessonBody,
  LessonDto,
  UpdateCourseBody,
  UpdateLessonBody,
} from "@/lib/dto/course.dto";

function toCourseSummary(c: Course): CourseSummaryDto {
  return {
    id: c.id,
    name: c.name,
    description: c.description,
    cover_image_url: c.coverImageUrl,
    status: c.status,
    created_at: c.createdAt.toISOString(),
    updated_at: c.updatedAt.toISOString(),
  };
}

function toLessonDto(l: Lesson): LessonDto {
  return {
    id: l.id,
    course_id: l.courseId,
    title: l.title,
    description: l.description,
    order_index: l.orderIndex,
    created_at: l.createdAt.toISOString(),
    updated_at: l.updatedAt.toISOString(),
  };
}

export async function createCourse(
  teacherId: string,
  role: UserRole,
  body: CreateCourseBody,
): Promise<CourseSummaryDto> {
  if (role !== UserRole.TEACHER) {
    throw new ApiError(403, "FORBIDDEN", "Only teachers can create courses");
  }
  const name = body.name?.trim();
  if (!name) {
    throw new ApiError(400, "VALIDATION_ERROR", "name is required");
  }
  const c = await prisma.course.create({
    data: {
      teacherId,
      name,
      description: body.description?.trim() || null,
      coverImageUrl: body.cover_image_url?.trim() || null,
      status: CourseStatus.DRAFT,
    },
  });
  return toCourseSummary(c);
}

export async function listMyCourses(
  userId: string,
  role: UserRole,
): Promise<CourseSummaryDto[]> {
  if (role === UserRole.TEACHER) {
    const rows = await prisma.course.findMany({
      where: { teacherId: userId, isDeleted: false },
      orderBy: { updatedAt: "desc" },
    });
    return rows.map(toCourseSummary);
  }
  if (role === UserRole.STUDENT) {
    const rows = await prisma.course.findMany({
      where: {
        isDeleted: false,
        enrollments: { some: { studentId: userId } },
      },
      orderBy: { updatedAt: "desc" },
    });
    return rows.map(toCourseSummary);
  }
  throw new ApiError(403, "FORBIDDEN", "Course list not available for this role");
}

export async function getCourseForUser(
  userId: string,
  role: UserRole,
  courseId: string,
): Promise<CourseSummaryDto> {
  const course = await getCourseIfMember(userId, role, courseId);
  return toCourseSummary(course);
}

export async function updateCourse(
  userId: string,
  role: UserRole,
  courseId: string,
  body: UpdateCourseBody,
): Promise<CourseSummaryDto> {
  const course = await assertTeacherOfCourse(userId, role, courseId);
  const data: {
    name?: string;
    description?: string | null;
    coverImageUrl?: string | null;
  } = {};
  if (body.name !== undefined) {
    const n = body.name.trim();
    if (!n) throw new ApiError(400, "VALIDATION_ERROR", "name cannot be empty");
    data.name = n;
  }
  if (body.description !== undefined) {
    data.description = body.description?.trim() || null;
  }
  if (body.cover_image_url !== undefined) {
    data.coverImageUrl = body.cover_image_url?.trim() || null;
  }
  const updated = await prisma.course.update({
    where: { id: course.id },
    data,
  });
  return toCourseSummary(updated);
}

export async function publishCourse(
  userId: string,
  role: UserRole,
  courseId: string,
): Promise<CourseSummaryDto> {
  await assertTeacherOfCourse(userId, role, courseId);
  const updated = await prisma.course.update({
    where: { id: courseId },
    data: { status: CourseStatus.PUBLISHED },
  });
  return toCourseSummary(updated);
}

export async function archiveCourse(
  userId: string,
  role: UserRole,
  courseId: string,
): Promise<CourseSummaryDto> {
  await assertTeacherOfCourse(userId, role, courseId);
  const updated = await prisma.course.update({
    where: { id: courseId },
    data: { status: CourseStatus.ARCHIVED },
  });
  return toCourseSummary(updated);
}

export async function joinCourse(
  studentId: string,
  role: UserRole,
  courseId: string,
): Promise<{ course_id: string; enrolled_at: string }> {
  if (role !== UserRole.STUDENT) {
    throw new ApiError(403, "FORBIDDEN", "Only students can join courses");
  }
  assertUuid(courseId, "course_id");
  const course = await prisma.course.findFirst({
    where: { id: courseId, isDeleted: false },
  });
  if (!course) {
    throw new ApiError(404, "NOT_FOUND", "Course not found");
  }
  if (course.status !== CourseStatus.PUBLISHED) {
    throw new ApiError(
      403,
      "FORBIDDEN",
      "Course is not published; enrollment is not open",
    );
  }
  const existing = await prisma.courseEnrollment.findUnique({
    where: {
      courseId_studentId: { courseId, studentId: studentId },
    },
  });
  if (existing) {
    throw new ApiError(409, "CONFLICT", "Already enrolled in this course");
  }
  const en = await prisma.courseEnrollment.create({
    data: { courseId, studentId: studentId },
  });
  return {
    course_id: courseId,
    enrolled_at: en.enrolledAt.toISOString(),
  };
}

export async function deleteCourse(
  userId: string,
  role: UserRole,
  courseId: string,
): Promise<void> {
  await assertTeacherOfCourse(userId, role, courseId);
  await prisma.course.update({
    where: { id: courseId },
    data: { isDeleted: true },
  });
}

export async function listLessons(
  userId: string,
  role: UserRole,
  courseId: string,
): Promise<LessonDto[]> {
  await getCourseIfMember(userId, role, courseId);
  const rows = await prisma.lesson.findMany({
    where: { courseId, isDeleted: false },
    orderBy: { orderIndex: "asc" },
  });
  return rows.map(toLessonDto);
}

export async function createLesson(
  userId: string,
  role: UserRole,
  courseId: string,
  body: CreateLessonBody,
): Promise<LessonDto> {
  await assertTeacherOfCourse(userId, role, courseId);
  const title = body.title?.trim();
  if (!title) {
    throw new ApiError(400, "VALIDATION_ERROR", "title is required");
  }
  const l = await prisma.lesson.create({
    data: {
      courseId,
      title,
      description: body.description?.trim() || null,
      orderIndex: body.order_index,
    },
  });
  return toLessonDto(l);
}

export async function updateLesson(
  userId: string,
  role: UserRole,
  courseId: string,
  lessonId: string,
  body: UpdateLessonBody,
): Promise<LessonDto> {
  await assertTeacherOfCourse(userId, role, courseId);
  assertUuid(lessonId, "lesson_id");
  const existing = await prisma.lesson.findFirst({
    where: { id: lessonId, courseId, isDeleted: false },
  });
  if (!existing) {
    throw new ApiError(404, "NOT_FOUND", "Lesson not found");
  }
  const data: { title?: string; description?: string | null; orderIndex?: number } =
    {};
  if (body.title !== undefined) {
    const t = body.title.trim();
    if (!t) throw new ApiError(400, "VALIDATION_ERROR", "title cannot be empty");
    data.title = t;
  }
  if (body.description !== undefined) {
    data.description = body.description?.trim() || null;
  }
  if (body.order_index !== undefined) {
    data.orderIndex = body.order_index;
  }
  const l = await prisma.lesson.update({
    where: { id: lessonId },
    data,
  });
  return toLessonDto(l);
}

export async function deleteLesson(
  userId: string,
  role: UserRole,
  courseId: string,
  lessonId: string,
): Promise<void> {
  await assertTeacherOfCourse(userId, role, courseId);
  assertUuid(lessonId, "lesson_id");
  const existing = await prisma.lesson.findFirst({
    where: { id: lessonId, courseId, isDeleted: false },
  });
  if (!existing) {
    throw new ApiError(404, "NOT_FOUND", "Lesson not found");
  }
  await prisma.lesson.update({
    where: { id: lessonId },
    data: { isDeleted: true },
  });
}
