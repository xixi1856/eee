import { describe, expect, it } from "vitest";
import type { RagQueueTask } from "@/lib/queue/ragTask";

describe("RagQueueTask contract", () => {
  it("contains only stream-safe fields (no trusted course_id/minio_path)", () => {
    const t: RagQueueTask = {
      task_id: "550e8400-e29b-41d4-a716-446655440000",
      material_id: "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
      operation: "parse_and_index",
      created_at: "2026-05-10T00:00:00.000Z",
    };
    const raw = JSON.stringify(t);
    expect(raw).not.toContain("course_id");
    expect(raw).not.toContain("minio_path");
    expect(raw).not.toContain("file_type");
    expect(JSON.parse(raw).material_id).toBe(t.material_id);
    expect(JSON.parse(raw).operation).toBe("parse_and_index");
  });
});
