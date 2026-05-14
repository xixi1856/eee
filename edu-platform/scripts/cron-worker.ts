#!/usr/bin/env tsx
/**
 * Cron Worker — standalone Node.js process that consumes the Redis Stream
 * `edu:cron:stream` and runs the TS ReAct agent for each job.
 *
 * Usage:  tsx scripts/cron-worker.ts
 * Or via docker-compose `cron-worker` service.
 *
 * Path aliases (@/) are resolved by tsx via tsconfig.json paths.
 */

import "dotenv/config";
import { randomUUID } from "crypto";

// ---------------------------------------------------------------------------
// Bootstrap path resolution for tsx when running from edu-platform/
// ---------------------------------------------------------------------------
import { createRequire } from "module";
import { fileURLToPath } from "url";
import * as path from "path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);

// Ensure process.cwd() is edu-platform/ so @/ resolves correctly
// tsx reads tsconfig.json paths from process.cwd()

// ---------------------------------------------------------------------------
// Imports (using relative paths to avoid any alias resolution issues at startup)
// ---------------------------------------------------------------------------

import { PrismaClient } from "@prisma/client";
import { createClient } from "redis";

const prisma = new PrismaClient();

// ---------------------------------------------------------------------------
// Redis helpers
// ---------------------------------------------------------------------------

const REDIS_URL = process.env.REDIS_URL ?? "redis://localhost:6379";
const STREAM_NAME = process.env.CRON_STREAM_NAME ?? "edu:cron:stream";
const GROUP_NAME = process.env.CRON_STREAM_GROUP ?? "edu-cron-workers";
const CONSUMER_NAME = `cron-worker-${process.pid}`;

let redisClient: ReturnType<typeof createClient> | null = null;

async function getRedisClient() {
  if (redisClient?.isOpen) return redisClient;
  const c = createClient({ url: REDIS_URL });
  c.on("error", (err) => console.error("[CronWorker] Redis error:", err));
  await c.connect();
  redisClient = c;
  return c;
}

async function ensureConsumerGroup() {
  const r = await getRedisClient();
  try {
    await r.xGroupCreate(STREAM_NAME, GROUP_NAME, "0", { MKSTREAM: true });
    console.log(`[CronWorker] Consumer group "${GROUP_NAME}" created.`);
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes("BUSYGROUP")) {
      // Group already exists — fine
    } else {
      throw err;
    }
  }
}

// ---------------------------------------------------------------------------
// Agent runner
// ---------------------------------------------------------------------------

async function runAgentForJob(
  runId: string,
  jobId: string,
  prompt: string,
): Promise<{ output: string; toolCalls: unknown[] }> {
  // Dynamic imports to avoid loading Next.js internals until needed
  const { createReActStream } = await import("../lib/agent/react-loop.js");
  const { buildAgentConfig, getMemoryCoordinator, getSkillsLoader } = await import(
    "../lib/agent/setup.js"
  );
  const { promptBuilder } = await import("../lib/agent/prompt-builder.js");
  const { toolRegistry } = await import("../lib/agent/tools/index.js");

  const config = buildAgentConfig();
  // Cron jobs run unattended — auto-approve all tools that would normally
  // require user confirmation in an interactive chat session.
  config.approvalMode = "auto";
  const coordinator = getMemoryCoordinator();
  const skills = getSkillsLoader().load();
  const memoryBlock = "";
  const sessionId = `cron-${jobId}-${runId}`;

  const stream = createReActStream({
    userMessage: prompt,
    config,
    toolRegistry,
    ctx: {
      userId: `cron-system`,
      sessionId,
      accessibleCourseIds: [],
      courseId: null,
      lessonId: null,
      traceId: null,
      debugTrace: false,
    },
    coordinator,
    promptBuilder,
    skills,
    profile: null,
    memoryBlock,
    history: [],
  });

  // Consume the ReadableStream and collect text + tool calls
  const dec = new TextDecoder();
  let buf = "";
  let fullText = "";
  const toolCalls: unknown[] = [];

  const reader = stream.getReader();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      for (;;) {
        const idx = buf.indexOf("\n\n");
        if (idx === -1) break;
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const dataLine = block.split("\n").find((l) => l.startsWith("data: "));
        if (!dataLine) continue;
        const jsonStr = dataLine.slice("data: ".length);
        let ev: { type: string; [k: string]: unknown };
        try {
          ev = JSON.parse(jsonStr) as typeof ev;
        } catch {
          continue;
        }
        if (ev.type === "text" && typeof ev.content === "string") {
          fullText += ev.content;
        } else if (ev.type === "tool_result") {
          toolCalls.push({ name: ev.name, success: ev.success, durationMs: ev.duration_ms });
        }
      }
    }
  } finally {
    reader.releaseLock();
  }

  return { output: fullText, toolCalls };
}

// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------

async function processMessage(
  runId: string,
  jobId: string,
  prompt: string,
  msgId: string,
  redis: ReturnType<typeof createClient>,
) {
  const startedAt = new Date();
  console.log(`[CronWorker] Starting job=${jobId} run=${runId}`);

  try {
    const { output, toolCalls } = await runAgentForJob(runId, jobId, prompt);
    await prisma.cronJobRun.update({
      where: { id: runId },
      data: {
        status: "success",
        output,
        toolCalls: toolCalls as object[],
        finishedAt: new Date(),
      },
    });
    console.log(
      `[CronWorker] Done job=${jobId} run=${runId} ` +
        `(${Date.now() - startedAt.getTime()}ms, ${output.length} chars)`,
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`[CronWorker] Error job=${jobId} run=${runId}:`, err);
    await prisma.cronJobRun
      .update({
        where: { id: runId },
        data: { status: "failed", errorMessage: msg.slice(0, 2000), finishedAt: new Date() },
      })
      .catch(() => {});
  } finally {
    // Always ACK to prevent re-delivery loop
    await redis.xAck(STREAM_NAME, GROUP_NAME, msgId).catch((e) => {
      console.error("[CronWorker] xAck failed:", e);
    });
  }
}

async function main() {
  console.log(`[CronWorker] Starting — stream=${STREAM_NAME} group=${GROUP_NAME} consumer=${CONSUMER_NAME}`);
  await ensureConsumerGroup();

  const redis = await getRedisClient();

  // Reclaim any PEL messages from previous crashed workers (pending > 30s)
  try {
    const pending = await redis.xAutoClaim(
      STREAM_NAME,
      GROUP_NAME,
      CONSUMER_NAME,
      30_000,
      "0-0",
    );
    for (const msg of pending.messages) {
      if (!msg) continue;
      const { run_id, job_id, prompt } = msg.message as Record<string, string>;
      if (run_id && job_id && prompt) {
        await processMessage(run_id, job_id, prompt, msg.id, redis);
      } else {
        await redis.xAck(STREAM_NAME, GROUP_NAME, msg.id).catch(() => {});
      }
    }
  } catch (err) {
    console.warn("[CronWorker] PEL reclaim skipped:", err);
  }

  console.log("[CronWorker] Entering main read loop...");
  for (;;) {
    let results: Array<{ name: string; messages: Array<{ id: string; message: Record<string, string> }> }>;
    try {
      results = (await redis.xReadGroup(
        GROUP_NAME,
        CONSUMER_NAME,
        [{ key: STREAM_NAME, id: ">" }],
        { COUNT: 1, BLOCK: 5_000 },
      )) as typeof results ?? [];
    } catch (err) {
      console.error("[CronWorker] xReadGroup error:", err);
      await new Promise((r) => setTimeout(r, 2_000));
      continue;
    }

    for (const stream of results ?? []) {
      for (const msg of stream.messages) {
        const fields = msg.message as Record<string, string>;
        const { run_id, job_id, prompt } = fields;
        if (!run_id || !job_id || !prompt) {
          await redis.xAck(STREAM_NAME, GROUP_NAME, msg.id).catch(() => {});
          continue;
        }
        await processMessage(run_id, job_id, prompt, msg.id, redis);
      }
    }
  }
}

// Graceful shutdown
process.on("SIGTERM", async () => {
  console.log("[CronWorker] SIGTERM received, shutting down...");
  await prisma.$disconnect().catch(() => {});
  await redisClient?.quit().catch(() => {});
  process.exit(0);
});

process.on("SIGINT", async () => {
  await prisma.$disconnect().catch(() => {});
  await redisClient?.quit().catch(() => {});
  process.exit(0);
});

main().catch((err) => {
  console.error("[CronWorker] Fatal:", err);
  process.exit(1);
});
