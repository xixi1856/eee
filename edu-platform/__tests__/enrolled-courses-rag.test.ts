import { describe, expect, it, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";
import { GET } from "@/app/api/v1/internal/enrolled-courses-rag/route";

const { findUniqueMock, findManyCoursesMock, findManyEnrollMock, findFirstUserMock } = vi.hoisted(
  () => ({
    findUniqueMock: vi.fn(),
    findManyCoursesMock: vi.fn(),
    findManyEnrollMock: vi.fn(),
    findFirstUserMock: vi.fn(),
  }),
);

vi.mock("@/lib/db", () => ({
  prisma: {
    agentIdentityMapping: { findUnique: findUniqueMock },
    course: { findMany: findManyCoursesMock },
    courseEnrollment: { findMany: findManyEnrollMock },
    user: { findFirst: findFirstUserMock },
  },
}));

vi.mock("@/lib/config", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/config")>();
  return {
    ...actual,
    getInternalApiKeyOrNull: () => "k".repeat(20),
  };
});

function req(url: string, key?: string) {
  return new NextRequest(url, {
    headers: key ? { "x-internal-key": key } : {},
  });
}

describe("GET /api/v1/internal/enrolled-courses-rag", () => {
  beforeEach(() => {
    findUniqueMock.mockReset();
    findManyCoursesMock.mockReset();
    findManyEnrollMock.mockReset();
    findFirstUserMock.mockReset();
    findUniqueMock.mockResolvedValue(null);
    findFirstUserMock.mockResolvedValue({ role: "STUDENT" });
    findManyEnrollMock.mockResolvedValue([{ courseId: "550e8400-e29b-41d4-a716-446655440000" }]);
    findManyCoursesMock.mockResolvedValue([]);
  });

  it("returns 401 when key invalid", async () => {
    const u = new URL("http://localhost/api");
    u.searchParams.set("user_id", "u1");
    const res = await GET(req(u.toString(), "bad"));
    expect(res.status).toBe(401);
  });

  it("returns course_ids for student enrollments", async () => {
    const u = new URL("http://localhost/api");
    u.searchParams.set("user_id", "student-platform-uuid");
    const res = await GET(req(u.toString(), "k".repeat(20)));
    expect(res.status).toBe(200);
    const body = (await res.json()) as { course_ids: string[] };
    expect(body.course_ids).toEqual(["550e8400-e29b-41d4-a716-446655440000"]);
    expect(findManyEnrollMock).toHaveBeenCalled();
  });
});
