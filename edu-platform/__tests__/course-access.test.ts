import { describe, expect, it } from "vitest";
import { assertUuid } from "@/lib/course-access";
import { ApiError } from "@/lib/http/api-error";

describe("assertUuid", () => {
  it("accepts lowercase uuid v4", () => {
    expect(() =>
      assertUuid("550e8400-e29b-41d4-a716-446655440000"),
    ).not.toThrow();
  });

  it("rejects invalid id", () => {
    expect(() => assertUuid("not-a-uuid")).toThrow(ApiError);
  });
});
