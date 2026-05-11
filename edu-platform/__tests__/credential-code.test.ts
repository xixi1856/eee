import { beforeEach, describe, expect, it, vi } from "vitest";

describe("credential-code", () => {
  beforeEach(() => {
    vi.stubEnv("CREDENTIAL_CODE_PEPPER", "test-pepper-16chars!!");
  });

  it("hashes deterministically", async () => {
    const { hashCredentialCode } = await import("@/lib/credential-code");
    expect(hashCredentialCode("aB3cD7eF")).toBe(hashCredentialCode("aB3cD7eF"));
  });

  it("generates 8-char code", async () => {
    const { generatePlainCredentialCode } = await import(
      "@/lib/credential-code"
    );
    const c = generatePlainCredentialCode();
    expect(c).toHaveLength(8);
    expect(c).toMatch(/^[A-Za-z0-9]+$/);
  });
});
