/**
 * Node-only instrumentation startup.
 * Isolated from instrumentation.ts so Edge bundle never touches Node modules.
 */

import { startCronScheduler } from "@/lib/cron/scheduler";

const g = globalThis as Record<string, unknown>;
if (!g.__cronSchedulerStarted) {
  g.__cronSchedulerStarted = true;
  startCronScheduler();
}
