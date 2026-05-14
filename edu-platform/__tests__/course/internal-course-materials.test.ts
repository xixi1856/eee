import { describe, expect, it, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";
import { GET } from "@/app/api/v1/internal/course-materials/route";
import { hasCourseRagAccess } from "@/lib/course-access";

const { findUniqueMock, findManyMaterialMock } = vi.hoisted(() => ({
  findUniqueMock: vi.fn(),
  findManyMaterialMock: vi.fn(),
}));

vi.mock("@/lib/db", () => ({
  prisma: {
    agentIdentityMapping: {
      findUnique: findUniqueMock,
    },
    material: {
      findMany: findManyMaterialMock,
    },
  },
}));

vi.mock("@/lib/config", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/config")>();
  return {
    ...actual,
    getInternalApiKeyOrNull: () => "k".repeat(20),
  };
});

vi.mock("@/lib/course-access", () => ({
  hasCourseRagAccess: vi.fn(),
}));

const courseId = "550e8400-e29b-41d4-a716-446655440000";
const userId = "6ba7b810-9dad-11d1-80b4-00c04fd430c8";

function req(url: string, key?: string) {
  return new NextRequest(url, {
    headers: key ? { "x-internal-key": key } : {},
  });
}

describe("GET /api/v1/internal/course-materials", () => {
  beforeEach(() => {
    vi.mocked(hasCourseRagAccess).mockReset();
    findUniqueMock.mockReset();
    findManyMaterialMock.mockReset();
    findUniqueMock.mockResolvedValue(null);
    findManyMaterialMock.mockResolvedValue([]);
  });

  it("returns 401 when internal key mismatches", async () => {
    const u = new URL("http://localhost/api");
    u.searchParams.set("course_id", courseId);
    u.searchParams.set("user_id", userId);
    const res = await GET(req(u.toString(), "wrong-key"));
    expect(res.status).toBe(401);
  });

  it("returns 403 when user has no course access", async () => {
    vi.mocked(hasCourseRagAccess).mockResolvedValue(false);
    const u = new URL("http://localhost/api");
    u.searchParams.set("course_id", courseId);
    u.searchParams.set("user_id", userId);
    const res = await GET(req(u.toString(), "k".repeat(20)));
    expect(res.status).toBe(403);
  });

  it("returns ready materials when access is granted", async () => {
    vi.mocked(hasCourseRagAccess).mockResolvedValue(true);
    findManyMaterialMock.mockResolvedValue([
      { id: "m1", originalFilename: "课程介绍.ppt" },
      { id: "m2", originalFilename: "第一章讲义.pdf" },
    ]);

    const u = new URL("http://localhost/api");
    u.searchParams.set("course_id", courseId);
    u.searchParams.set("user_id", userId);
    const res = await GET(req(u.toString(), "k".repeat(20)));
    expect(res.status).toBe(200);

    const body = (await res.json()) as {
      materials: Array<{ id: string; original_filename: string }>;
    };
    expect(body.materials).toEqual([
      { id: "m1", original_filename: "课程介绍.ppt" },
      { id: "m2", original_filename: "第一章讲义.pdf" },
    ]);

    expect(hasCourseRagAccess).toHaveBeenCalledWith(userId, courseId);
    expect(findManyMaterialMock).toHaveBeenCalledWith({
      where: {
        courseId,
        isDeleted: false,
        status: "READY",
      },
      orderBy: [{ updatedAt: "desc" }],
      select: {
        id: true,
        originalFilename: true,
      },
    });
  });
});
