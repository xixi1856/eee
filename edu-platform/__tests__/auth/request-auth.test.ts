import { describe, expect, it, vi, afterEach } from "vitest";
import { NextRequest } from "next/server";

describe("getClientIp", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("with TRUST_PROXY_HOPS=0 ignores X-Forwarded-For and uses X-Real-IP", async () => {
    vi.stubEnv("TRUST_PROXY_HOPS", "0");
    const { getClientIp } = await import("@/lib/request-auth");
    const req = new NextRequest("http://localhost/", {
      headers: {
        "x-forwarded-for": "6.6.6.6, 1.2.3.4",
        "x-real-ip": "203.0.113.1",
      },
    });
    expect(getClientIp(req)).toBe("203.0.113.1");
  });

  it("with TRUST_PROXY_HOPS=0 and no X-Real-IP returns unknown", async () => {
    vi.stubEnv("TRUST_PROXY_HOPS", "0");
    const { getClientIp } = await import("@/lib/request-auth");
    const req = new NextRequest("http://localhost/", {
      headers: { "x-forwarded-for": "6.6.6.6" },
    });
    expect(getClientIp(req)).toBe("unknown");
  });

  it("with TRUST_PROXY_HOPS=1 uses rightmost client segment of XFF", async () => {
    vi.stubEnv("TRUST_PROXY_HOPS", "1");
    const { getClientIp } = await import("@/lib/request-auth");
    const req = new NextRequest("http://localhost/", {
      headers: { "x-forwarded-for": "198.51.100.2, 198.51.100.1" },
    });
    expect(getClientIp(req)).toBe("198.51.100.2");
  });
});
