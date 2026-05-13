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
  objectExists,
  putObjectStream,
} from "@/lib/minio";
import {
  isOfficeMaterialFileType,
  legacyConvertedPdfObjectKey,
  previewPdfObjectKey,
} from "@/lib/material-office";
import { enqueueRagTask, type RagQueueTask } from "@/lib/queue/ragTask";
import { getRedis } from "@/lib/redis";
import { getMaterialStaleSec } from "@/lib/config";
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

const STALE_PROCESSING_STATUSES = new Set<MaterialStatus>([
  MaterialStatus.PARSING,
  MaterialStatus.INDEXING,
  MaterialStatus.PARSED,
]);

/**
 * If a material is stuck in a processing state with no DB update beyond the stale
 * threshold, mark it FAILED in-place so the frontend shows failure immediately,
 * without waiting for the worker to restart.
 *
 * Returns the updated material if it was marked stale, otherwise the original.
 */
async function reconcileStaleProcessingMaterial(m: Material): Promise<Material> {
  if (!STALE_PROCESSING_STATUSES.has(m.status)) return m;
  const staleSec = getMaterialStaleSec();
  const ageMs = Date.now() - m.updatedAt.getTime();
  if (ageMs < staleSec * 1000) return m;
  // Mark as FAILED — best effort; ignore race with worker re-claiming the row.
  await prisma.material.updateMany({
    where: {
      id: m.id,
      isDeleted: false,
      status: { in: [...STALE_PROCESSING_STATUSES] },
    },
    data: {
      status: MaterialStatus.FAILED,
      statusMessage: "WORKER_ABANDONED: worker was likely interrupted or crashed",
    },
  });
  const refreshed = await prisma.material.findFirst({
    where: { id: m.id, isDeleted: false },
  });
  return refreshed ?? m;
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

function officePreviewKeys(m: Material): [string, string] {
  return [
    previewPdfObjectKey(m.minioPath),
    legacyConvertedPdfObjectKey(m.minioPath, m.id),
  ];
}

async function markOfficePreviewReadyAndMaybeQueueParse(m: Material): Promise<void> {
  const moved = await prisma.material.updateMany({
    where: {
      id: m.id,
      isDeleted: false,
      previewPdfStatus: {
        in: [
          MaterialPreviewPdfStatus.PENDING,
          MaterialPreviewPdfStatus.FAILED,
        ],
      },
    },
    data: {
      previewPdfStatus: MaterialPreviewPdfStatus.READY,
      statusMessage: null,
    },
  });
  if (moved.count < 1) {
    return;
  }
  if (m.status !== MaterialStatus.UPLOADED || !getRedisUrl()) {
    return;
  }
  try {
    await enqueueRagTaskWithRetry({
      task_id: randomUUID(),
      material_id: m.id,
      operation: "parse_and_index",
      created_at: new Date().toISOString(),
      text_only: true,
      skip_kg: true,
    });
  } catch (e) {
    const detail = e instanceof Error ? e.message : String(e);
    await prisma.material.updateMany({
      where: {
        id: m.id,
        isDeleted: false,
        status: MaterialStatus.UPLOADED,
      },
      data: {
        statusMessage: `PREVIEW_READY_PARSE_QUEUE_FAILED: ${detail.slice(0, 500)}`,
      },
    });
  }
}

async function reconcileOfficePreviewIfObjectReady(m: Material): Promise<Material> {
  if (!isOfficeMaterialFileType(m.fileType)) {
    return m;
  }
  if (
    m.previewPdfStatus !== MaterialPreviewPdfStatus.PENDING &&
    m.previewPdfStatus !== MaterialPreviewPdfStatus.FAILED
  ) {
    return m;
  }

  const keys = officePreviewKeys(m);
  let previewExists = false;
  for (const key of keys) {
    try {
      if (await objectExists(key)) {
        previewExists = true;
        break;
      }
    } catch {
      return m;
    }
  }
  if (!previewExists) {
    return m;
  }

  await markOfficePreviewReadyAndMaybeQueueParse(m);
  const refreshed = await prisma.material.findFirst({
    where: { id: m.id, isDeleted: false },
  });
  return refreshed ?? m;
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
  const reconciled = await Promise.all(
    rows.map((m) =>
      reconcileOfficePreviewIfObjectReady(m).then(reconcileStaleProcessingMaterial)
    ),
  );
  return { materials: reconciled.map(toSummary) };
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
  textOnly?: boolean;
  skipKg?: boolean;
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

  const textOnly = params.textOnly ?? true;
  const skipKg = params.skipKg ?? true;
  const task: RagQueueTask = isOfficeMaterialFileType(fileType)
    ? {
        task_id: randomUUID(),
        material_id: materialId,
        operation: "convert_preview",
        created_at: new Date().toISOString(),
        text_only: textOnly,
        skip_kg: skipKg,
      }
    : {
        task_id: randomUUID(),
        material_id: materialId,
        operation: "parse_and_index",
        created_at: new Date().toISOString(),
        text_only: textOnly,
        skip_kg: skipKg,
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

/** Queue index-only retry (worker must have local MinerU output under ``output_dir``). */
export async function retryMaterialIndex(
  userId: string,
  role: UserRole,
  courseId: string,
  materialId: string,
  textOnly?: boolean,
  skipKg?: boolean,
): Promise<void> {
  assertUuid(materialId, "material_id");
  assertUuid(courseId, "course_id");
  await assertTeacherOfCourse(userId, role, courseId);
  if (!getRedisUrl()) {
    throw new ApiError(
      503,
      "SERVICE_UNAVAILABLE",
      "REDIS_URL is required to queue RAG index retry",
    );
  }
  const m = await prisma.material.findFirst({
    where: { id: materialId, courseId, isDeleted: false },
  });
  if (!m) {
    throw new ApiError(404, "NOT_FOUND", "Material not found");
  }
  if (m.status !== MaterialStatus.FAILED) {
    throw new ApiError(
      409,
      "CONFLICT",
      "Only materials in FAILED status can retry indexing from cached parse output",
      { status: m.status },
    );
  }
  const task: RagQueueTask = {
    task_id: randomUUID(),
    material_id: materialId,
    operation: "index_only",
    created_at: new Date().toISOString(),
    text_only: textOnly ?? true,
    skip_kg: skipKg ?? true,
  };
  await enqueueRagTaskWithRetry(task);
}

/** Redis key used to signal the Python worker to abort processing. TTL = 2 h. */
export function ragCancelKey(materialId: string): string {
  return `edu:rag:cancel:${materialId}`;
}

const CANCEL_KEY_TTL_SEC = 7200;

/**
 * Cancel an in-progress RAG task for a material.
 *
 * Steps:
 * 1. Validate caller is teacher of the owning course.
 * 2. Set a Redis signal key so the Python worker aborts at its next checkpoint.
 * 3. Soft-delete the material (isDeleted=true) so it disappears from listings.
 * 4. Enqueue delete_material to clean up MinIO / LightRAG vectors after the worker stops.
 *
 * Safe to call on UPLOADED/PARSING/PARSED/INDEXING. Returns 404 for unknown or
 * already-deleted materials, 409 for READY (use DELETE instead).
 */
export async function cancelMaterialProcessing(
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

  const processingStatuses: MaterialStatus[] = [
    MaterialStatus.UPLOADED,
    MaterialStatus.PARSING,
    MaterialStatus.PARSED,
    MaterialStatus.INDEXING,
  ];
  if (!processingStatuses.includes(m.status)) {
    throw new ApiError(
      409,
      "CONFLICT",
      "Only materials currently being processed can be cancelled",
      { status: m.status },
    );
  }
  if (!getRedisUrl()) {
    throw new ApiError(
      503,
      "SERVICE_UNAVAILABLE",
      "REDIS_URL is required to send cancel signal",
    );
  }

  // 1. Set Redis interrupt signal before soft-deleting so the worker sees it ASAP.
  const redis = await getRedis();
  await redis.set(ragCancelKey(materialId), "1", { EX: CANCEL_KEY_TTL_SEC });

  // 2. Soft-delete the material record.
  await prisma.material.update({
    where: { id: materialId },
    data: { isDeleted: true },
  });

  // 3. Enqueue cleanup (MinIO + LightRAG vectors). Best-effort — if this fails the
  //    material is already hidden and the signal is set; log but don't surface error.
  const task: RagQueueTask = {
    task_id: randomUUID(),
    material_id: materialId,
    operation: "delete_material",
    created_at: new Date().toISOString(),
  };
  try {
    await enqueueRagTaskWithRetry(task);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    await prisma.material.update({
      where: { id: materialId },
      data: { statusMessage: `CANCEL_CLEANUP_QUEUE_FAILED: ${msg}` },
    }).catch(() => {});
  }

  // 4. Clean up MinIO objects eagerly (fire-and-forget).
  await deleteObject(m.minioPath).catch(() => {});
  if (isOfficeMaterialFileType(m.fileType)) {
    await deleteObject(previewPdfObjectKey(m.minioPath)).catch(() => {});
    await deleteObject(legacyConvertedPdfObjectKey(m.minioPath, m.id)).catch(() => {});
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
  const m = await reconcileStaleProcessingMaterial(
    await reconcileOfficePreviewIfObjectReady(
      await assertMaterialReadAccess(userId, role, materialId),
    ),
  );
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

async function enqueuePreviewRepairIfFailed(materialId: string): Promise<boolean> {
  if (!getRedisUrl()) {
    return false;
  }
  const moved = await prisma.material.updateMany({
    where: {
      id: materialId,
      isDeleted: false,
      previewPdfStatus: MaterialPreviewPdfStatus.FAILED,
    },
    data: {
      previewPdfStatus: MaterialPreviewPdfStatus.PENDING,
      statusMessage: null,
    },
  });
  if (moved.count < 1) {
    return false;
  }
  try {
    await enqueueRagTaskWithRetry({
      task_id: randomUUID(),
      material_id: materialId,
      operation: "repair_preview",
      created_at: new Date().toISOString(),
    });
  } catch (e) {
    const detail = e instanceof Error ? e.message : String(e);
    await prisma.material.updateMany({
      where: {
        id: materialId,
        isDeleted: false,
        previewPdfStatus: MaterialPreviewPdfStatus.PENDING,
      },
      data: {
        previewPdfStatus: MaterialPreviewPdfStatus.FAILED,
        statusMessage: `PREVIEW_REPAIR_QUEUE_FAILED: ${detail.slice(0, 500)}`,
      },
    });
    throw e;
  }
  return true;
}

/** Office inline preview: try `preview.pdf`, then legacy `{materialId}.pdf`. */
async function readOfficePreviewStreamWithFallback(
  m: Material,
): Promise<Awaited<ReturnType<typeof getObjectStream>>> {
  const keys = officePreviewKeys(m);
  for (const objectKey of keys) {
    try {
      return await readObjectStreamForMaterial(objectKey);
    } catch (e) {
      const err = e instanceof ApiError ? e : mapStorageReadError(e);
      if (err.status === 404 && err.code === "NOT_FOUND") {
        continue;
      }
      throw err;
    }
  }
  let reconcileFailed = false;
  let reconcileDetail: string | undefined;
  let repairQueued = false;
  let repairQueueDetail: string | undefined;
  if (m.previewPdfStatus === MaterialPreviewPdfStatus.READY) {
    try {
      await prisma.material.update({
        where: { id: m.id },
        data: {
          previewPdfStatus: MaterialPreviewPdfStatus.FAILED,
          statusMessage: "预览 PDF 在存储中不存在，请重新上传或等待转换完成。",
        },
      });
    } catch (e) {
      reconcileFailed = true;
      reconcileDetail = e instanceof Error ? e.message : String(e);
    }
    try {
      repairQueued = await enqueuePreviewRepairIfFailed(m.id);
    } catch (e) {
      repairQueueDetail = e instanceof Error ? e.message : String(e);
    }
  }
  throw new ApiError(
    425,
    "PREVIEW_NOT_READY",
    "Preview PDF is repairing",
    {
      tried_keys: keys,
      reconciled_to_failed: m.previewPdfStatus === MaterialPreviewPdfStatus.READY && !reconcileFailed,
      reconcile_failed: reconcileFailed,
      reconcile_error: reconcileFailed ? reconcileDetail?.slice(0, 500) : undefined,
      repair_queued: repairQueued,
      repair_queue_error: repairQueueDetail ? repairQueueDetail.slice(0, 500) : undefined,
    },
  );
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
      // Self-heal stale state: preview object may already exist while DB still says PENDING/FAILED.
      try {
        const preview = await readOfficePreviewStreamWithFallback(m);
        await markOfficePreviewReadyAndMaybeQueueParse(m);
        return {
          body: preview.body,
          contentType: preview.contentType || "application/pdf",
          contentDisposition: "inline",
        };
      } catch (e) {
        if (!(e instanceof ApiError) || e.code !== "PREVIEW_NOT_READY") {
          throw e;
        }
      }

      let repairQueued = false;
      let repairQueueError: string | undefined;
      if (ps === MaterialPreviewPdfStatus.FAILED) {
        try {
          repairQueued = await enqueuePreviewRepairIfFailed(m.id);
        } catch (e) {
          repairQueueError = e instanceof Error ? e.message : String(e);
        }
      }
      throw new ApiError(425, "PREVIEW_NOT_READY", "Preview PDF is not ready yet", {
        preview_pdf_status: ps,
        repair_queued: repairQueued,
        repair_queue_error: repairQueueError ? repairQueueError.slice(0, 500) : undefined,
      });
    }
    const { body, contentType } = await readOfficePreviewStreamWithFallback(m);
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
