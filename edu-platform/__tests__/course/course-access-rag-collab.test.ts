import { describe, expect, it, vi, beforeEach } from "vitest";

const { findFirstCourseMock, findUniqueCollabMock, findUniqueEnrollMock } =
  vi.hoisted(() => ({
    findFirstCourseMock: vi.fn(),
    findUniqueCollabMock: vi.fn(),
    findUniqueEnrollMock: vi.fn(),
  }));

vi.mock("@/lib/db", () => ({
  prisma: {
    course: { findFirst: findFirstCourseMock },
    courseCollaborator: { findUnique: findUniqueCollabMock },
    courseEnrollment: { findUnique: findUniqueEnrollMock },
  },
}));

import { hasCourseRagAccess } from "@/lib/course-access";

const COURSE_ID = "550e8400-e29b-41d4-a716-446655440000";
const OWNER_ID = "660e8400-e29b-41d4-a716-446655440001";
const COLLAB_ID = "770e8400-e29b-41d4-a716-446655440002";

describe("hasCourseRagAccess", () => {
  beforeEach(() => {
    findFirstCourseMock.mockReset();
    findUniqueCollabMock.mockReset();
    findUniqueEnrollMock.mockReset();
  });

  it("returns true for course owner", async () => {
    findFirstCourseMock.mockResolvedValue({
      id: COURSE_ID,
      teacherId: OWNER_ID,
      isDeleted: false,
    });
    expect(await hasCourseRagAccess(OWNER_ID, COURSE_ID)).toBe(true);
    expect(findUniqueCollabMock).not.toHaveBeenCalled();
  });

  it("returns true for collaborator", async () => {
    findFirstCourseMock.mockResolvedValue({
      id: COURSE_ID,
      teacherId: OWNER_ID,
      isDeleted: false,
    });
    findUniqueCollabMock.mockResolvedValue({
      id: "880e8400-e29b-41d4-a716-446655440003",
    });
    expect(await hasCourseRagAccess(COLLAB_ID, COURSE_ID)).toBe(true);
    expect(findUniqueCollabMock).toHaveBeenCalled();
  });

  it("returns true for enrolled student", async () => {
    findFirstCourseMock.mockResolvedValue({
      id: COURSE_ID,
      teacherId: OWNER_ID,
      isDeleted: false,
    });
    findUniqueCollabMock.mockResolvedValue(null);
    findUniqueEnrollMock.mockResolvedValue({ id: "x" });
    const studentId = "990e8400-e29b-41d4-a716-446655440004";
    expect(await hasCourseRagAccess(studentId, COURSE_ID)).toBe(true);
  });

  it("returns false when no access", async () => {
    findFirstCourseMock.mockResolvedValue({
      id: COURSE_ID,
      teacherId: OWNER_ID,
      isDeleted: false,
    });
    findUniqueCollabMock.mockResolvedValue(null);
    findUniqueEnrollMock.mockResolvedValue(null);
    const stranger = "aa0e8400-e29b-41d4-a716-446655440005";
    expect(await hasCourseRagAccess(stranger, COURSE_ID)).toBe(false);
  });
});
