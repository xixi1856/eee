import { getCronStreamName } from "@/lib/config";
import { getRedis } from "@/lib/redis";

export type CronQueueTask = {
  run_id: string;
  job_id: string;
  prompt: string;
  created_at: string;
};

export async function enqueueCronTask(task: CronQueueTask): Promise<void> {
  const redis = await getRedis();
  await redis.xAdd(getCronStreamName(), "*", {
    run_id: task.run_id,
    job_id: task.job_id,
    prompt: task.prompt,
    created_at: task.created_at,
  });
}
