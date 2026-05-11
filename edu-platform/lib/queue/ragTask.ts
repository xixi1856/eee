import { getRagTaskStreamName } from "@/lib/config";
import { getRedis } from "@/lib/redis";

/** Minimal payload: DB is source of truth; worker loads course_id/minio_path from materials. */
export type RagQueueTask = {
  task_id: string;
  material_id: string;
  operation: "parse_and_index" | "delete_material";
  created_at: string;
};

export async function enqueueRagTask(task: RagQueueTask): Promise<void> {
  const redis = await getRedis();
  const stream = getRagTaskStreamName();
  await redis.xAdd(stream, "*", {
    task_id: task.task_id,
    material_id: task.material_id,
    operation: task.operation,
    created_at: task.created_at,
  });
}
