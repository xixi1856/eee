-- CreateEnum
CREATE TYPE "AssignmentStatus" AS ENUM ('GENERATING', 'FAILED', 'DRAFT', 'PUBLISHED', 'ARCHIVED');

-- DropIndex
DROP INDEX "qa_logs_hit_materials_idx";

-- CreateTable
CREATE TABLE "assignments" (
    "id" UUID NOT NULL,
    "course_id" UUID NOT NULL,
    "created_by" UUID NOT NULL,
    "title" TEXT NOT NULL,
    "description" TEXT,
    "status" "AssignmentStatus" NOT NULL DEFAULT 'GENERATING',
    "error_message" TEXT,
    "blueprint" JSONB,
    "questions" JSONB,
    "quality_report" JSONB,
    "deadline" TIMESTAMP(3),
    "published_at" TIMESTAMP(3),
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "assignments_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "assignments_course_id_status_idx" ON "assignments"("course_id", "status");

-- CreateIndex
CREATE INDEX "assignments_course_id_created_at_idx" ON "assignments"("course_id", "created_at" DESC);

-- AddForeignKey
ALTER TABLE "assignments" ADD CONSTRAINT "assignments_course_id_fkey" FOREIGN KEY ("course_id") REFERENCES "courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "assignments" ADD CONSTRAINT "assignments_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;
