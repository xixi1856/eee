import type { CourseStatus } from "@prisma/client";

export type CreateCourseBody = {
  name: string;
  description?: string | null;
  cover_image_url?: string | null;
};

export type UpdateCourseBody = {
  name?: string;
  description?: string | null;
  cover_image_url?: string | null;
};

export type CourseSummaryDto = {
  id: string;
  name: string;
  description: string | null;
  cover_image_url: string | null;
  status: CourseStatus;
  created_at: string;
  updated_at: string;
  /** Present only for course owner or collaborator when status is PUBLISHED. */
  share_code?: string | null;
};

export type JoinByShareCodeBody = {
  share_code: string;
};

export type LessonDto = {
  id: string;
  course_id: string;
  title: string;
  description: string | null;
  order_index: number;
  created_at: string;
  updated_at: string;
};

export type CreateLessonBody = {
  title: string;
  description?: string | null;
  order_index: number;
};

export type UpdateLessonBody = {
  title?: string;
  description?: string | null;
  order_index?: number;
};

export type ReorderLessonsBody = {
  orders: { id: string; order_index: number }[];
};
