import { describe, expect, it, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";
import { GET } from "@/app/api/v1/internal/course-rag-access/route";
import { hasCourseRagAccess } from "@/lib/course-access";

const { findUniqueMock } = vi.hoisted(() => ({
  findUniqueMock: vi.fn(),
}));

vi.mock("@/lib/db", () => ({
  prisma: {
    agentIdentityMapping: {
      findUnique: findUniqueMock,
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

describe("GET /api/v1/internal/course-rag-access", () => {
  beforeEach(() => {
    vi.mocked(hasCourseRagAccess).mockReset();
    findUniqueMock.mockReset();
    findUniqueMock.mockResolvedValue(null);
  });

  it("returns 401 when internal key mismatches", async () => {
    const u = new URL("http://localhost/api");
    u.searchParams.set("course_id", courseId);
    u.searchParams.set("user_id", userId);
    const res = await GET(req(u.toString(), "wrong-key"));
    expect(res.status).toBe(401);
  });

  it("returns 400 when course_id missing", async () => {
    const u = new URL("http://localhost/api");
    u.searchParams.set("user_id", userId);
    const res = await GET(req(u.toString(), "k".repeat(20)));
    expect(res.status).toBe(400);
  });

  it("returns access true when hasCourseRagAccess is true", async () => {
    vi.mocked(hasCourseRagAccess).mockResolvedValue(true);
    const u = new URL("http://localhost/api");
    u.searchParams.set("course_id", courseId);
    u.searchParams.set("user_id", userId);
    const res = await GET(req(u.toString(), "k".repeat(20)));
    expect(res.status).toBe(200);
    const body = (await res.json()) as { access: boolean };
    expect(body.access).toBe(true);
    expect(findUniqueMock).toHaveBeenCalledWith({
      where: { agentUserId: userId },
      select: { platformUserId: true },
    });
    expect(hasCourseRagAccess).toHaveBeenCalledWith(userId, courseId);
  });

  it("resolves agent_user_id to platform user id before enrollment check", async () => {
    const agentUserId = "agent-bound-user-1";
    const platformUserId = "6ba7b810-9dad-11d1-80b4-00c04fd430c8";
    findUniqueMock.mockResolvedValueOnce({ platformUserId });
    vi.mocked(hasCourseRagAccess).mockResolvedValue(true);
    const u = new URL("http://localhost/api");
    u.searchParams.set("course_id", courseId);
    u.searchParams.set("user_id", agentUserId);
    const res = await GET(req(u.toString(), "k".repeat(20)));
    expect(res.status).toBe(200);
    expect(findUniqueMock).toHaveBeenCalledWith({
      where: { agentUserId: agentUserId },
      select: { platformUserId: true },
    });
    expect(hasCourseRagAccess).toHaveBeenCalledWith(platformUserId, courseId);
  });
});
