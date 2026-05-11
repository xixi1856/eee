import { randomUUID } from "node:crypto";
import { Readable } from "node:stream";
import type { ReadableStream } from "node:stream/web";
import {
  MaterialPreviewPdfStatus,
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
import {
  deleteObject,
  getObjectStream,
  putObjectStream,
} from "@/lib/minio";
import {
  isOfficeMaterialFileType,
  legacyConvertedPdfObjectKey,
  previewPdfObjectKey,
} from "@/lib/material-office";
import { enqueueRagTask, type RagQueueTask } from "@/lib/queue/ragTask";
import type {
  MaterialCreatedDto,
  MaterialDetailDto,
  MaterialSummaryDto,
} from "@/lib/dto/material.dto";
import { MATERIAL_UPLOAD_ALLOWED_EXT_SET } from "@/lib/material-upload-allowed";
import { mapStorageReadError } from "@/lib/material-storage-errors";

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
const ALLOWED_EXT = MATERIAL_UPLOAD_ALLOWED_EXT_SET;

function extToFileType(ext: string): string {
  const e = ext.toLowerCase();
  if (e === "jpg" || e === "jpeg" || e === "png" || e === "webp") return "image";
  if (e === "ppt") return "ppt";
  if (e === "pptx") return "pptx";
  if (e === "doc") return "doc";
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
    preview_pdf_status: m.previewPdfStatus,
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
      allowed: [...ALLOWED_EXT].sort(),
    });
  }
  const fileType = extToFileType(ext);
  const materialId = randomUUID();
  const safeName = params.originalFilename.replace(/[^a-zA-Z0-9._-]/g, "_");
  const minioPath = `materials/${params.courseId}/${materialId}/${safeName}`;
  const previewPdfStatus = isOfficeMaterialFileType(fileType)
    ? MaterialPreviewPdfStatus.PENDING
    : MaterialPreviewPdfStatus.NA;

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
    throw new ApiError(
      503,
      "SERVICE_UNAVAILABLE",
      "Object storage upload failed",
      { detail: e instanceof Error ? e.message : String(e) },
    );
  }

  let material: Material;
  try {
    material = await prisma.material.create({
      data: {
        id: materialId,
        courseId: params.courseId,
        lessonId: params.lessonId || null,
        originalFilename: params.originalFilename,
        fileType,
        fileSize: params.contentLength,
        minioPath,
        previewPdfStatus,
        status: MaterialStatus.UPLOADED,
      } as never,
    });
  } catch (e) {
    await deleteObject(minioPath).catch(() => {});
    throw new ApiError(
      500,
      "INTERNAL_ERROR",
      "Failed to persist material after upload",
      { detail: e instanceof Error ? e.message : String(e) },
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
  if (isOfficeMaterialFileType(m.fileType)) {
    await deleteObject(previewPdfObjectKey(m.minioPath)).catch(() => {});
    await deleteObject(
      legacyConvertedPdfObjectKey(m.minioPath, m.id),
    ).catch(() => {});
  }
}

function guessContentTypeByFileType(fileType: string): string {
  const ft = fileType.toLowerCase();
  if (ft === "pdf") return "application/pdf";
  if (ft === "md") return "text/markdown; charset=utf-8";
  if (ft === "txt") return "text/plain; charset=utf-8";
  return "application/octet-stream";
}

export async function assertMaterialReadAccess(
  userId: string,
  role: UserRole,
  materialId: string,
): Promise<Material> {
  assertUuid(materialId, "material_id");
  const m = await prisma.material.findFirst({
    where: { id: materialId, isDeleted: false },
  });
  if (!m) {
    throw new ApiError(404, "NOT_FOUND", "Material not found");
  }
  await getCourseIfMember(userId, role, m.courseId);
  return m;
}

export async function getMaterialDetailDto(
  userId: string,
  role: UserRole,
  materialId: string,
): Promise<MaterialDetailDto> {
  const m = await assertMaterialReadAccess(userId, role, materialId);
  return {
    id: m.id,
    filename: m.originalFilename,
    file_type: m.fileType,
    lesson_id: m.lessonId ?? null,
    status: m.status,
    preview_pdf_status: m.previewPdfStatus,
    indexed_chunk_count: m.indexedChunkCount,
    created_at: m.createdAt.toISOString(),
    status_message: m.statusMessage,
  };
}

async function readObjectStreamForMaterial(
  objectKey: string,
): Promise<Awaited<ReturnType<typeof getObjectStream>>> {
  try {
    return await getObjectStream({ objectKey });
  } catch (e) {
    throw mapStorageReadError(e);
  }
}

export type OpenMaterialContentParams = {
  userId: string;
  role: UserRole;
  materialId: string;
  /** `original` streams the uploaded object as attachment (for download). */
  variant: "inline" | "original";
};

export type OpenMaterialContentResult = {
  /** S3 web stream (cast for ``NextResponse`` / BodyInit typing). */
  body: BodyInit;
  contentType: string;
  contentDisposition: string;
};

export async function openMaterialContentStream(
  params: OpenMaterialContentParams,
): Promise<OpenMaterialContentResult> {
  const m = await assertMaterialReadAccess(
    params.userId,
    params.role,
    params.materialId,
  );
  const ft = m.fileType.toLowerCase();

  if (params.variant === "original") {
    const { body, contentType } = await readObjectStreamForMaterial(m.minioPath);
    const ct = contentType || guessContentTypeByFileType(ft);
    const name = encodeURIComponent(m.originalFilename);
    return {
      body,
      contentType: ct,
      contentDisposition: `attachment; filename*=UTF-8''${name}`,
    };
  }

  if (isOfficeMaterialFileType(ft)) {
    const ps = m.previewPdfStatus;
    if (ps !== MaterialPreviewPdfStatus.READY) {
      throw new ApiError(425, "PREVIEW_NOT_READY", "Preview PDF is not ready yet", {
        preview_pdf_status: ps,
      });
    }
    const key = previewPdfObjectKey(m.minioPath);
    const { body, contentType } = await readObjectStreamForMaterial(key);
    return {
      body,
      contentType: contentType || "application/pdf",
      contentDisposition: "inline",
    };
  }

  if (ft === "pdf" || ft === "md" || ft === "txt") {
    const { body, contentType } = await readObjectStreamForMaterial(m.minioPath);
    return {
      body,
      contentType: contentType || guessContentTypeByFileType(ft),
      contentDisposition: "inline",
    };
  }

  throw new ApiError(
    400,
    "VALIDATION_ERROR",
    "Unsupported material type for inline preview",
  );
}
