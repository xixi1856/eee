import { Readable } from "node:stream";
import {
  MaterialPreviewPdfStatus,
  MaterialStatus,
  UserRole,
} from "@prisma/client";
import { beforeEach, describe, expect, it, vi } from "vitest";

const createMaterialMock = vi.fn();
const enqueueRagTaskMock = vi.fn();
const putObjectStreamMock = vi.fn();
const assertTeacherOfCourseMock = vi.fn();

vi.mock("@/lib/db", () => ({
  prisma: {
    material: {
      create: createMaterialMock,
    },
    lesson: {
      findFirst: vi.fn(),
    },
  },
}));

vi.mock("@/lib/config", () => ({
  getMaterialMaxUploadBytes: vi.fn(() => 1024 * 1024),
  getMinioConfig: vi.fn(() => ({ endpoint: "http://127.0.0.1:9000" })),
  getRedisUrl: vi.fn(() => "redis://localhost:6379"),
}));

vi.mock("@/lib/course-access", () => ({
  assertTeacherOfCourse: assertTeacherOfCourseMock,
  getCourseIfMember: vi.fn(),
  assertUuid: vi.fn(),
}));

vi.mock("@/lib/minio", () => ({
  deleteObject: vi.fn(),
  getObjectStream: vi.fn(),
  putObjectStream: putObjectStreamMock,
}));

vi.mock("@/lib/queue/ragTask", () => ({
  enqueueRagTask: enqueueRagTaskMock,
}));

describe("uploadMaterialStream", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createMaterialMock.mockResolvedValue({
      id: "mat-1",
      originalFilename: "slides.pptx",
      status: MaterialStatus.UPLOADED,
      createdAt: new Date("2026-05-13T00:00:00.000Z"),
      previewPdfStatus: MaterialPreviewPdfStatus.PENDING,
    });
    putObjectStreamMock.mockResolvedValue(undefined);
    enqueueRagTaskMock.mockResolvedValue(undefined);
    assertTeacherOfCourseMock.mockResolvedValue(undefined);
  });

  it("preserves skip_kg on initial office convert_preview task", async () => {
    const { uploadMaterialStream } = await import("@/lib/services/materialService");

    await uploadMaterialStream({
      teacherUserId: "teacher-1",
      role: UserRole.TEACHER,
      courseId: "course-1",
      originalFilename: "slides.pptx",
      contentType: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      contentLength: 128,
      body: Readable.from(["pptx"]),
      textOnly: false,
      skipKg: false,
    });

    expect(enqueueRagTaskMock).toHaveBeenCalledTimes(1);
    expect(enqueueRagTaskMock).toHaveBeenCalledWith(
      expect.objectContaining({
        operation: "convert_preview",
        text_only: false,
        skip_kg: false,
      }),
    );
  });
});