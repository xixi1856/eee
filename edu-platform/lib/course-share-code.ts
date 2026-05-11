import { randomInt } from "node:crypto";
import { prisma } from "@/lib/db";

/** Crockford base32 without I, L, O, U (10 chars). */
const ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
const DEFAULT_LEN = 10;
const MAX_ALLOC_ATTEMPTS = 40;

export function normalizeCourseShareCode(raw: string): string {
  return raw.trim().toUpperCase().replace(/\s+/g, "");
}

export function randomCourseShareCode(length = DEFAULT_LEN): string {
  let out = "";
  for (let i = 0; i < length; i++) {
    out += ALPHABET[randomInt(ALPHABET.length)]!;
  }
  return out;
}

/** Collision-resistant against existing `courses.share_code`. */
export async function allocateUniqueCourseShareCode(): Promise<string> {
  for (let i = 0; i < MAX_ALLOC_ATTEMPTS; i++) {
    const code = randomCourseShareCode();
    const clash = await prisma.course.findFirst({
      where: { shareCode: code },
      select: { id: true },
    });
    if (!clash) return code;
  }
  throw new Error("allocateUniqueCourseShareCode: exhausted attempts");
}
