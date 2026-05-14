import { describe, expect, it } from "vitest";
import { assertPasswordPolicy } from "@/lib/validation/password-policy";
import { ApiError } from "@/lib/http/api-error";

describe("assertPasswordPolicy", () => {
  it("accepts strong password", () => {
    expect(() => assertPasswordPolicy("Aa1!aaaa")).not.toThrow();
  });

  it("rejects short password", () => {
    expect(() => assertPasswordPolicy("Aa1!")).toThrow(ApiError);
  });

  it("rejects weak classes", () => {
    expect(() => assertPasswordPolicy("abcdefgh")).toThrow(ApiError);
  });
});
