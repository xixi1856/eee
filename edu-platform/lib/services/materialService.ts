import { randomUUID } from "node:crypto";
import { Readable } from "node:stream";
import type { ReadableStream } from "node:stream/web";
import {
  MaterialStatus,
  UserRole,
  type Material,
} from "@prisma/client";
import { prisma } from "@/lib/db";
import { ApiError } from "@/lib/http/api-error";
import {
  getMaterialMaxUploadBytes,
  getMinioConfig,
  getRedisUrl,
} from "@/lib/config";
import { assertTeacherOfCourse, getCourseIfMember, assertUuid } from "@/lib/course-access";
import { deleteObject, putObjectStream } from "@/lib/minio";
import { enqueueRagTask, type RagQueueTask } from "@/lib/queue/ragTask";
import type { MaterialCreatedDto, MaterialSummaryDto } from "@/lib/dto/material.dto";

async function enqueueRagTaskWithRetry(task: RagQueueTask, maxAttempts = 5): Promise<void> {
  let last: unknown;
  for (let i = 0; i < maxAttempts; i++) {
    try {
      await enqueueRagTask(task);
      return;
    } catch (e) {
      last = e;
      await new Promise((r) => setTimeout(r, 200 * (i + 1)));
    }
  }
  throw last;
}

/** Must match worker `parse_material` (see review_phase7 H4). */
const ALLOWED_EXT = new Set(["pdf", "md", "txt", "pptx", "docx"]);

function extToFileType(ext: string): string {
  const e = ext.toLowerCase();
  if (e === "jpg" || e === "jpeg" || e === "png" || e === "webp") return "image";
  if (e === "pptx") return "pptx";
  if (e === "docx") return "docx";
  return e;
}

function parseExtension(filename: string): string {
  const i = filename.lastIndexOf(".");
  if (i < 0) return "";
  return filename.slice(i + 1);
}

function toSummary(m: Material): MaterialSummaryDto {
  return {
    id: m.id,
    filename: m.originalFilename,
    file_type: m.fileType,
    lesson_id: m.lessonId ?? null,
    status: m.status,
    indexed_chunk_count: m.indexedChunkCount,
    created_at: m.createdAt.toISOString(),
    status_message: m.statusMessage,
  };
}

export async function listMaterials(
  userId: string,
  role: UserRole,
  courseId: string,
  filters: { status?: MaterialStatus },
): Promise<{ materials: MaterialSummaryDto[] }> {
  await getCourseIfMember(userId, role, courseId);
  const where: {
    courseId: string;
    isDeleted: boolean;
    status?: MaterialStatus;
  } = { courseId, isDeleted: false };
  if (filters.status) {
    where.status = filters.status;
  }
  const rows = await prisma.material.findMany({
    where,
    orderBy: { createdAt: "desc" },
  });
  return { materials: rows.map(toSummary) };
}

export async function uploadMaterialStream(params: {
  teacherUserId: string;
  role: UserRole;
  courseId: string;
  originalFilename: string;
  contentType: string | undefined;
  contentLength: number;
  body: ReadableStream<Uint8Array> | Readable;
  lessonId?: string | null;
}): Promise<MaterialCreatedDto> {
  try {
    getMinioConfig();
  } catch {
    throw new ApiError(
      503,
      "SERVICE_UNAVAILABLE",
      "Object storage is not configured",
    );
  }

  const max = getMaterialMaxUploadBytes();
  if (params.contentLength > max) {
    throw new ApiError(400, "VALIDATION_ERROR", "File too large", {
      max_bytes: max,
    });
  }
  if (!getRedisUrl()) {
    throw new ApiError(
      503,
      "SERVICE_UNAVAILABLE",
      "REDIS_URL is required for material processing",
    );
  }

  await assertTeacherOfCourse(params.teacherUserId, params.role, params.courseId);

  if (params.lessonId) {
    assertUuid(params.lessonId, "lesson_id");
    const lesson = await prisma.lesson.findFirst({
      where: {
        id: params.lessonId,
        courseId: params.courseId,
        isDeleted: false,
      },
    });
    if (!lesson) {
      throw new ApiError(404, "NOT_FOUND", "Lesson not found");
    }
  }

  const ext = parseExtension(params.originalFilename);
  if (!ext || !ALLOWED_EXT.has(ext.toLowerCase())) {
    throw new ApiError(400, "VALIDATION_ERROR", "Unsupported file type", {
      allowed: [...ALLOWED_EXT],
    });
  }
  const fileType = extToFileType(ext);
  const materialId = randomUUID();
  const safeName = params.originalFilename.replace(/[^a-zA-Z0-9._-]/g, "_");
  const minioPath = `materials/${params.courseId}/${materialId}/${safeName}`;

  const material = await prisma.material.create({
    data: {
      id: materialId,
      courseId: params.courseId,
      lessonId: params.lessonId || null,
      originalFilename: params.originalFilename,
      fileType,
      fileSize: params.contentLength,
      minioPath,
      status: MaterialStatus.UPLOADED,
    },
  });

  const nodeReadable =
    params.body instanceof Readable
      ? params.body
      : Readable.fromWeb(params.body as ReadableStream<Uint8Array>);

  try {
    await putObjectStream({
      objectKey: minioPath,
      body: nodeReadable,
      contentLength: params.contentLength,
      contentType: params.contentType,
    });
  } catch (e) {
    await prisma.material.update({
      where: { id: materialId },
      data: {
        status: MaterialStatus.FAILED,
        statusMessage: e instanceof Error ? e.message : "MinIO upload failed",
      },
    });
    throw new ApiError(
      503,
      "SERVICE_UNAVAILABLE",
      "Object storage upload failed",
    );
  }

  const task: RagQueueTask = {
    task_id: randomUUID(),
    material_id: materialId,
    operation: "parse_and_index",
    created_at: new Date().toISOString(),
  };
  await enqueueRagTaskWithRetry(task);

  return {
    id: material.id,
    original_filename: material.originalFilename,
    status: material.status,
    created_at: material.createdAt.toISOString(),
  };
}

export async function deleteMaterial(
  userId: string,
  role: UserRole,
  materialId: string,
): Promise<void> {
  assertUuid(materialId, "material_id");
  const m = await prisma.material.findFirst({
    where: { id: materialId, isDeleted: false },
    include: { course: true },
  });
  if (!m) {
    throw new ApiError(404, "NOT_FOUND", "Material not found");
  }
  await assertTeacherOfCourse(userId, role, m.courseId);
  if (!getRedisUrl()) {
    throw new ApiError(
      503,
      "SERVICE_UNAVAILABLE",
      "REDIS_URL is required to queue RAG cleanup",
    );
  }
  const task: RagQueueTask = {
    task_id: randomUUID(),
    material_id: materialId,
    operation: "delete_material",
    created_at: new Date().toISOString(),
  };
  await prisma.material.update({
    where: { id: materialId },
    data: { isDeleted: true },
  });
  try {
    await enqueueRagTaskWithRetry(task);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    await prisma.material.update({
      where: { id: materialId },
      data: { statusMessage: `RAG_DELETE_QUEUE_FAILED: ${msg}` },
    });
    throw new ApiError(
      503,
      "SERVICE_UNAVAILABLE",
      "Failed to queue RAG cleanup after delete",
      { detail: msg },
    );
  }
  await deleteObject(m.minioPath);
}
