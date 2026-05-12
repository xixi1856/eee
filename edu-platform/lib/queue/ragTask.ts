import { getRagTaskStreamName } from "@/lib/config";
import { getRedis } from "@/lib/redis";

/** Minimal payload: DB is source of truth; worker loads course_id/minio_path from materials. */
export type RagQueueTask = {
  task_id: string;
  material_id: string;
  operation: "parse_and_index" | "index_only" | "delete_material" | "repair_preview";
  created_at: string;
  text_only?: boolean;
};

export async function enqueueRagTask(task: RagQueueTask): Promise<void> {
  const redis = await getRedis();
  const stream = getRagTaskStreamName();
  const fields: Record<string, string> = {
    task_id: task.task_id,
    material_id: task.material_id,
    operation: task.operation,
    created_at: task.created_at,
  };
  if (typeof task.text_only === "boolean") {
    fields.text_only = task.text_only ? "true" : "false";
  }
  await redis.xAdd(stream, "*", {
    ...fields,
  });
}
