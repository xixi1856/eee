#!/usr/bin/env tsx
/**
 * One-time migration: import cron jobs from data/cron_jobs.json into PostgreSQL.
 * Run from edu-platform/ directory:
 *   tsx prisma/migrate-cron-jobs.ts
 */

import "dotenv/config";
import * as fs from "fs";
import * as path from "path";
import { PrismaClient } from "@prisma/client";

const prisma = new PrismaClient();

type LegacyCronJob = {
  id: string;
  prompt: string;
  schedule: string;
  created_at: string;
  status: string;
  last_run?: string | null;
  next_run?: string | null;
  output_dir?: string;
};

async function main() {
  const jsonPath = path.resolve(__dirname, "../../data/cron_jobs.json");
  if (!fs.existsSync(jsonPath)) {
    console.log(`[migrate-cron-jobs] File not found: ${jsonPath}`);
    process.exit(0);
  }

  const jobs: LegacyCronJob[] = JSON.parse(fs.readFileSync(jsonPath, "utf-8"));
  console.log(`[migrate-cron-jobs] Found ${jobs.length} jobs in cron_jobs.json`);

  let created = 0;
  let skipped = 0;

  for (const job of jobs) {
    const existing = await prisma.cronJob.findUnique({ where: { id: job.id } });
    if (existing) {
      console.log(`  skip (already exists): ${job.id}`);
      skipped++;
      continue;
    }

    // Map legacy status: "active" → "active", anything else → "paused"
    const status = job.status === "active" ? "active" : "paused";

    await prisma.cronJob.create({
      data: {
        id: job.id,
        userId: null, // no user association for migrated system jobs
        prompt: job.prompt,
        schedule: job.schedule,
        status,
        lastRunAt: job.last_run ? new Date(job.last_run) : null,
        nextRunAt: job.next_run ? new Date(job.next_run) : null,
        createdAt: job.created_at ? new Date(job.created_at) : new Date(),
      },
    });

    console.log(`  created: ${job.id} (schedule: ${job.schedule})`);
    created++;
  }

  console.log(`[migrate-cron-jobs] Done: ${created} created, ${skipped} skipped.`);
}

main()
  .catch((err) => {
    console.error("[migrate-cron-jobs] Error:", err);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
