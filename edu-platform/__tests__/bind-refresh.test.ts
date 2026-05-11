import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const { findUniqueMock, signChannelTokenMock } = vi.hoisted(() => ({
  findUniqueMock: vi.fn(),
  signChannelTokenMock: vi.fn(),
}));

vi.mock("@/lib/db", () => ({
  prisma: {
    agentIdentityMapping: {
      findUnique: findUniqueMock,
    },
  },
}));

vi.mock("@/lib/jwt", () => ({
  signChannelToken: signChannelTokenMock,
}));

vi.mock("@/lib/config", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/config")>();
  return {
    ...actual,
    getBindCredentialApiKey: () => "bind-key-16chars-min",
    getChannelTtlSec: () => 3600,
    getJwtSecret: () => "jwt-secret-16chars-min",
    getJwtIssuer: () => "edu-platform",
  };
});

import { POST } from "@/app/api/v1/bind/refresh/route";

function makeRequest(body: unknown, key: string | null = "bind-key-16chars-min") {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (key !== null) headers["x-platform-bind-key"] = key;
  return POST(
    new NextRequest("http://localhost/api/v1/bind/refresh", {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    }),
  );
}

describe("POST /api/v1/bind/refresh", () => {
  beforeEach(() => {
    findUniqueMock.mockReset();
    signChannelTokenMock.mockReset();
    signChannelTokenMock.mockResolvedValue("new-channel-token-jwt");
  });

  it("returns new channel_token when binding exists", async () => {
    findUniqueMock.mockResolvedValueOnce({
      platformUserId: "platform-uuid-001",
      agentUserId: "agent-user-001",
      channel: "wechat",
    });
    const res = await makeRequest({ agent_user_id: "agent-user-001" });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.channel_token).toBe("new-channel-token-jwt");
  });

  it("returns 404 when agent_user_id has no binding", async () => {
    findUniqueMock.mockResolvedValueOnce(null);
    const res = await makeRequest({ agent_user_id: "unbound-agent" });
    expect(res.status).toBe(404);
    const body = await res.json();
    expect(body.error.code).toBe("BIND_NOT_FOUND");
  });

  it("returns 400 when agent_user_id is missing", async () => {
    const res = await makeRequest({});
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error.code).toBe("VALIDATION_ERROR");
  });

  it("returns 401 when API key is wrong", async () => {
    const res = await makeRequest({ agent_user_id: "agent-user-001" }, "wrong-key-16chars");
    expect(res.status).toBe(401);
    const body = await res.json();
    expect(body.error.code).toBe("UNAUTHORIZED");
  });

  it("returns 401 when API key is missing", async () => {
    const res = await makeRequest({ agent_user_id: "agent-user-001" }, null);
    expect(res.status).toBe(401);
  });
});
