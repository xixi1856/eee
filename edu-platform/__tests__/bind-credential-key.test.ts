import { beforeEach, describe, expect, it, vi } from "vitest";

describe("verifyBindCredentialApiKey", () => {
  beforeEach(() => {
    vi.stubEnv("BIND_CREDENTIAL_API_KEY", "bind-key-16chars-min");
  });

  it("accepts exact key", async () => {
    const { verifyBindCredentialApiKey } = await import(
      "@/lib/bind-credential-key"
    );
    expect(verifyBindCredentialApiKey("bind-key-16chars-min")).toBe(true);
  });

  it("rejects wrong key", async () => {
    const { verifyBindCredentialApiKey } = await import(
      "@/lib/bind-credential-key"
    );
    expect(verifyBindCredentialApiKey("wrong-key-16chars")).toBe(false);
  });

  it("rejects null", async () => {
    const { verifyBindCredentialApiKey } = await import(
      "@/lib/bind-credential-key"
    );
    expect(verifyBindCredentialApiKey(null)).toBe(false);
  });
});
