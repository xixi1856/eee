import {
  MaterialPreviewPdfStatus,
  MaterialStatus,
  UserRole,
} from "@prisma/client";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  findManyMock,
  findFirstMock,
  updateManyMock,
  lessonFindFirstMock,
} = vi.hoisted(() => ({
  findManyMock: vi.fn(),
  findFirstMock: vi.fn(),
  updateManyMock: vi.fn(),
  lessonFindFirstMock: vi.fn(),
}));

const {
  getCourseIfMemberMock,
  assertTeacherOfCourseMock,
  assertUuidMock,
} = vi.hoisted(() => ({
  getCourseIfMemberMock: vi.fn(),
  assertTeacherOfCourseMock: vi.fn(),
  assertUuidMock: vi.fn(),
}));

const {
  objectExistsMock,
  getObjectStreamMock,
  putObjectStreamMock,
  deleteObjectMock,
} = vi.hoisted(() => ({
  objectExistsMock: vi.fn(),
  getObjectStreamMock: vi.fn(),
  putObjectStreamMock: vi.fn(),
  deleteObjectMock: vi.fn(),
}));

const { enqueueRagTaskMock } = vi.hoisted(() => ({
  enqueueRagTaskMock: vi.fn(),
}));

vi.mock("@/lib/db", () => ({
  prisma: {
    material: {
      findMany: findManyMock,
      findFirst: findFirstMock,
      updateMany: updateManyMock,
    },
    lesson: {
      findFirst: lessonFindFirstMock,
    },
  },
}));

vi.mock("@/lib/course-access", () => ({
  getCourseIfMember: getCourseIfMemberMock,
  assertTeacherOfCourse: assertTeacherOfCourseMock,
  assertUuid: assertUuidMock,
}));

vi.mock("@/lib/minio", () => ({
  objectExists: objectExistsMock,
  getObjectStream: getObjectStreamMock,
  putObjectStream: putObjectStreamMock,
  deleteObject: deleteObjectMock,
}));

vi.mock("@/lib/queue/ragTask", () => ({
  enqueueRagTask: enqueueRagTaskMock,
}));

vi.mock("@/lib/config", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/config")>();
  return {
    ...actual,
    getMinioConfig: vi.fn(() => ({ endpoint: "http://127.0.0.1:9000" })),
    getMaterialMaxUploadBytes: vi.fn(() => 1024 * 1024),
    getRedisUrl: vi.fn(() => "redis://localhost:6379"),
  };
});

describe("materialService preview reconcile", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getCourseIfMemberMock.mockResolvedValue(undefined);
    assertTeacherOfCourseMock.mockResolvedValue(undefined);
    assertUuidMock.mockImplementation(() => undefined);
    objectExistsMock.mockResolvedValue(true);
    enqueueRagTaskMock.mockResolvedValue(undefined);
    updateManyMock.mockResolvedValue({ count: 1 });
    lessonFindFirstMock.mockResolvedValue(null);
  });

  it("listMaterials reconciles office preview and enqueues parse when still uploaded", async () => {
    const createdAt = new Date("2026-05-13T00:00:00.000Z");
    const row = {
      id: "a25e17dd-63cc-4075-9e07-0fd8bd3f43ee",
      courseId: "c1",
      lessonId: null,
      originalFilename: "slides.pptx",
      fileType: "pptx",
      fileSize: 123,
      minioPath: "materials/c1/a25e17dd-63cc-4075-9e07-0fd8bd3f43ee/slides.pptx",
      previewPdfStatus: MaterialPreviewPdfStatus.PENDING,
      status: MaterialStatus.UPLOADED,
      statusMessage: null,
      indexedChunkCount: 0,
      createdAt,
      updatedAt: createdAt,
      isDeleted: false,
    };

    findManyMock.mockResolvedValue([row]);
    findFirstMock.mockResolvedValue({
      ...row,
      previewPdfStatus: MaterialPreviewPdfStatus.READY,
    });

    const { listMaterials } = await import("@/lib/services/materialService");
    const out = await listMaterials(
      "teacher-1",
      UserRole.TEACHER,
      "c1",
      {},
    );

    expect(out.materials).toHaveLength(1);
    expect(out.materials[0]?.preview_pdf_status).toBe(
      MaterialPreviewPdfStatus.READY,
    );
    expect(enqueueRagTaskMock).toHaveBeenCalledWith(
      expect.objectContaining({
        material_id: row.id,
        operation: "parse_and_index",
      }),
    );
  });

  it("openMaterialContentStream forces application/pdf for office preview", async () => {
    const now = new Date("2026-05-14T00:00:00.000Z");
    findFirstMock.mockResolvedValue({
      id: "a25e17dd-63cc-4075-9e07-0fd8bd3f43ee",
      courseId: "c1",
      lessonId: null,
      originalFilename: "slides.ppt",
      fileType: "ppt",
      fileSize: 123,
      minioPath: "materials/c1/a25e17dd-63cc-4075-9e07-0fd8bd3f43ee/slides.ppt",
      previewPdfStatus: MaterialPreviewPdfStatus.READY,
      status: MaterialStatus.INDEXING,
      statusMessage: null,
      indexedChunkCount: 0,
      createdAt: now,
      updatedAt: now,
      isDeleted: false,
    });
    getObjectStreamMock.mockResolvedValue({
      body: "PDF_BYTES",
      contentType: "application/octet-stream",
      contentLength: 42,
      contentRange: undefined,
      isPartial: false,
    });

    const { openMaterialContentStream } = await import("@/lib/services/materialService");
    const out = await openMaterialContentStream({
      userId: "teacher-1",
      role: UserRole.TEACHER,
      materialId: "a25e17dd-63cc-4075-9e07-0fd8bd3f43ee",
      variant: "inline",
    });

    expect(out.contentType).toBe("application/pdf");
    expect(out.contentDisposition).toBe("inline");
    expect(getObjectStreamMock).toHaveBeenCalledWith(
      expect.objectContaining({
        objectKey: "materials/c1/a25e17dd-63cc-4075-9e07-0fd8bd3f43ee/preview.pdf",
      }),
    );
  });

  it("openMaterialContentStream forwards range for native pdf and returns partial metadata", async () => {
    const now = new Date("2026-05-14T00:00:00.000Z");
    findFirstMock.mockResolvedValue({
      id: "a25e17dd-63cc-4075-9e07-0fd8bd3f43ee",
      courseId: "c1",
      lessonId: null,
      originalFilename: "chapter.pdf",
      fileType: "pdf",
      fileSize: 123,
      minioPath: "materials/c1/a25e17dd-63cc-4075-9e07-0fd8bd3f43ee/chapter.pdf",
      previewPdfStatus: MaterialPreviewPdfStatus.NA,
      status: MaterialStatus.READY,
      statusMessage: null,
      indexedChunkCount: 0,
      createdAt: now,
      updatedAt: now,
      isDeleted: false,
    });
    getObjectStreamMock.mockResolvedValue({
      body: "PDF_BYTES",
      contentType: "application/pdf",
      contentLength: 1024,
      contentRange: "bytes 0-1023/8192",
      isPartial: true,
    });

    const { openMaterialContentStream } = await import("@/lib/services/materialService");
    const out = await openMaterialContentStream({
      userId: "teacher-1",
      role: UserRole.TEACHER,
      materialId: "a25e17dd-63cc-4075-9e07-0fd8bd3f43ee",
      variant: "inline",
      range: "bytes=0-1023",
    });

    expect(getObjectStreamMock).toHaveBeenCalledWith(
      expect.objectContaining({
        objectKey: "materials/c1/a25e17dd-63cc-4075-9e07-0fd8bd3f43ee/chapter.pdf",
        range: "bytes=0-1023",
      }),
    );
    expect(out.contentRange).toBe("bytes 0-1023/8192");
    expect(out.isPartial).toBe(true);
  });

  it("openMaterialContentStream forwards range for office preview object", async () => {
    const now = new Date("2026-05-14T00:00:00.000Z");
    findFirstMock.mockResolvedValue({
      id: "a25e17dd-63cc-4075-9e07-0fd8bd3f43ee",
      courseId: "c1",
      lessonId: null,
      originalFilename: "slides.ppt",
      fileType: "ppt",
      fileSize: 123,
      minioPath: "materials/c1/a25e17dd-63cc-4075-9e07-0fd8bd3f43ee/slides.ppt",
      previewPdfStatus: MaterialPreviewPdfStatus.READY,
      status: MaterialStatus.READY,
      statusMessage: null,
      indexedChunkCount: 0,
      createdAt: now,
      updatedAt: now,
      isDeleted: false,
    });
    getObjectStreamMock.mockResolvedValue({
      body: "PDF_BYTES",
      contentType: "application/pdf",
      contentLength: 1024,
      contentRange: "bytes 0-1023/8192",
      isPartial: true,
    });

    const { openMaterialContentStream } = await import("@/lib/services/materialService");
    const out = await openMaterialContentStream({
      userId: "teacher-1",
      role: UserRole.TEACHER,
      materialId: "a25e17dd-63cc-4075-9e07-0fd8bd3f43ee",
      variant: "inline",
      range: "bytes=0-1023",
    });

    expect(getObjectStreamMock).toHaveBeenCalledWith(
      expect.objectContaining({
        objectKey: "materials/c1/a25e17dd-63cc-4075-9e07-0fd8bd3f43ee/preview.pdf",
        range: "bytes=0-1023",
      }),
    );
    expect(out.contentRange).toBe("bytes 0-1023/8192");
    expect(out.isPartial).toBe(true);
  });
});
