import {
  CourseStatus,
  UserRole,
  type Course,
  type Lesson,
} from "@prisma/client";
import { prisma } from "@/lib/db";
import { ApiError } from "@/lib/http/api-error";
import {
  assertUuid,
  getCourseIfMember,
  assertTeacherOfCourse,
  assertCourseOwner,
  isCourseCollaborator,
} from "@/lib/course-access";
import {
  allocateUniqueCourseShareCode,
  normalizeCourseShareCode,
} from "@/lib/course-share-code";
import type {
  CourseSummaryDto,
  CreateCourseBody,
  CreateLessonBody,
  LessonDto,
  ReorderLessonsBody,
  UpdateCourseBody,
  UpdateLessonBody,
} from "@/lib/dto/course.dto";

async function courseToSummaryDto(
  c: Course,
  viewerId: string,
  role: UserRole,
): Promise<CourseSummaryDto> {
  const base: CourseSummaryDto = {
    id: c.id,
    name: c.name,
    description: c.description,
    cover_image_url: c.coverImageUrl,
    status: c.status,
    created_at: c.createdAt.toISOString(),
    updated_at: c.updatedAt.toISOString(),
  };
  if (
    role === UserRole.TEACHER &&
    c.status === CourseStatus.PUBLISHED &&
    c.shareCode
  ) {
    if (
      c.teacherId === viewerId ||
      (await isCourseCollaborator(c.id, viewerId))
    ) {
      base.share_code = c.shareCode;
    }
  }
  return base;
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
  return courseToSummaryDto(c, teacherId, role);
}

export async function listMyCourses(
  userId: string,
  role: UserRole,
): Promise<CourseSummaryDto[]> {
  if (role === UserRole.TEACHER) {
    const rows = await prisma.course.findMany({
      where: {
        isDeleted: false,
        OR: [
          { teacherId: userId },
          { collaborators: { some: { teacherId: userId } } },
        ],
      },
      orderBy: { updatedAt: "desc" },
    });
    return Promise.all(rows.map((c) => courseToSummaryDto(c, userId, role)));
  }
  if (role === UserRole.STUDENT) {
    const rows = await prisma.course.findMany({
      where: {
        isDeleted: false,
        enrollments: { some: { studentId: userId } },
      },
      orderBy: { updatedAt: "desc" },
    });
    return Promise.all(rows.map((c) => courseToSummaryDto(c, userId, role)));
  }
  throw new ApiError(403, "FORBIDDEN", "Course list not available for this role");
}

export async function getCourseForUser(
  userId: string,
  role: UserRole,
  courseId: string,
): Promise<CourseSummaryDto> {
  const course = await getCourseIfMember(userId, role, courseId);
  return courseToSummaryDto(course, userId, role);
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
  return courseToSummaryDto(updated, userId, role);
}

export async function publishCourse(
  userId: string,
  role: UserRole,
  courseId: string,
): Promise<CourseSummaryDto> {
  await assertCourseOwner(userId, role, courseId);
  const existing = await prisma.course.findFirst({
    where: { id: courseId, isDeleted: false },
  });
  if (!existing) {
    throw new ApiError(404, "NOT_FOUND", "Course not found");
  }
  if (existing.status === CourseStatus.ARCHIVED) {
    throw new ApiError(
      409,
      "CONFLICT",
      "Cannot publish an archived course",
    );
  }
  if (existing.status === CourseStatus.PUBLISHED) {
    return courseToSummaryDto(existing, userId, role);
  }
  const shareCode =
    existing.shareCode ?? (await allocateUniqueCourseShareCode());
  const updated = await prisma.course.update({
    where: { id: courseId },
    data: {
      status: CourseStatus.PUBLISHED,
      shareCode,
    },
  });
  return courseToSummaryDto(updated, userId, role);
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
  return courseToSummaryDto(updated, userId, role);
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

export type JoinByShareCodeResult =
  | { course_id: string; enrolled_at: string; role: "student" }
  | { course_id: string; joined_at: string; role: "collaborator" };

export async function joinCourseByShareCode(
  userId: string,
  role: UserRole,
  rawCode: string,
): Promise<JoinByShareCodeResult> {
  if (role === UserRole.ADMIN) {
    throw new ApiError(403, "FORBIDDEN", "Admins cannot join courses this way");
  }
  const code = normalizeCourseShareCode(rawCode);
  if (!code) {
    throw new ApiError(400, "VALIDATION_ERROR", "share_code is required");
  }
  const course = await prisma.course.findFirst({
    where: {
      shareCode: code,
      isDeleted: false,
      status: CourseStatus.PUBLISHED,
    },
  });
  if (!course) {
    throw new ApiError(
      404,
      "NOT_FOUND",
      "Invalid share code or course is not open for joining",
    );
  }

  if (role === UserRole.STUDENT) {
    const existing = await prisma.courseEnrollment.findUnique({
      where: {
        courseId_studentId: { courseId: course.id, studentId: userId },
      },
    });
    if (existing) {
      throw new ApiError(409, "CONFLICT", "Already enrolled in this course");
    }
    const en = await prisma.courseEnrollment.create({
      data: { courseId: course.id, studentId: userId },
    });
    return {
      course_id: course.id,
      enrolled_at: en.enrolledAt.toISOString(),
      role: "student",
    };
  }

  if (role !== UserRole.TEACHER) {
    throw new ApiError(403, "FORBIDDEN", "Only students or teachers can join with a share code");
  }
  if (course.teacherId === userId) {
    throw new ApiError(409, "CONFLICT", "Already the course owner");
  }
  const existingCollab = await prisma.courseCollaborator.findUnique({
    where: {
      courseId_teacherId: { courseId: course.id, teacherId: userId },
    },
  });
  if (existingCollab) {
    throw new ApiError(409, "CONFLICT", "Already a collaborator on this course");
  }
  const row = await prisma.courseCollaborator.create({
    data: { courseId: course.id, teacherId: userId },
  });
  return {
    course_id: course.id,
    joined_at: row.createdAt.toISOString(),
    role: "collaborator",
  };
}

export async function deleteCourse(
  userId: string,
  role: UserRole,
  courseId: string,
): Promise<void> {
  await assertCourseOwner(userId, role, courseId);
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

export async function reorderLessons(
  userId: string,
  role: UserRole,
  courseId: string,
  body: ReorderLessonsBody,
): Promise<void> {
  await assertTeacherOfCourse(userId, role, courseId);
  if (!Array.isArray(body.orders) || body.orders.length === 0) return;
  // Verify all lessons belong to this course (prevent cross-course order tampering)
  const ids = body.orders.map((o) => o.id);
  const existing = await prisma.lesson.findMany({
    where: { id: { in: ids }, courseId, isDeleted: false },
    select: { id: true },
  });
  const existingIds = new Set(existing.map((l) => l.id));
  for (const o of body.orders) {
    if (!existingIds.has(o.id)) {
      throw new ApiError(404, "NOT_FOUND", `Lesson ${o.id} not found in this course`);
    }
  }
  await prisma.$transaction(
    body.orders.map((o) =>
      prisma.lesson.update({
        where: { id: o.id },
        data: { orderIndex: o.order_index },
      }),
    ),
  );
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
