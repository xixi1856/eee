import { describe, expect, it } from "vitest";
import {
  normalizeCourseShareCode,
  randomCourseShareCode,
} from "../lib/course-share-code";

const ALPHABET = /^[0-9A-Z]+$/;

describe("normalizeCourseShareCode", () => {
  it("trims and uppercases", () => {
    expect(normalizeCourseShareCode("  ab12  ")).toBe("AB12");
  });

  it("removes internal spaces", () => {
    expect(normalizeCourseShareCode("A B 1 2")).toBe("AB12");
  });
});

describe("randomCourseShareCode", () => {
  it("uses fixed length and allowed alphabet", () => {
    const code = randomCourseShareCode(10);
    expect(code).toHaveLength(10);
    expect(ALPHABET.test(code)).toBe(true);
    expect(code).not.toMatch(/[ILOU]/);
  });
});
