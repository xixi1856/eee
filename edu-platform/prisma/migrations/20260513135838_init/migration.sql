-- CreateEnum
CREATE TYPE "UserRole" AS ENUM ('STUDENT', 'TEACHER', 'ADMIN');

-- CreateEnum
CREATE TYPE "CourseStatus" AS ENUM ('DRAFT', 'PUBLISHED', 'ARCHIVED');

-- CreateEnum
CREATE TYPE "MaterialStatus" AS ENUM ('UPLOADED', 'PARSING', 'PARSED', 'INDEXING', 'READY', 'FAILED');

-- CreateEnum
CREATE TYPE "MaterialPreviewPdfStatus" AS ENUM ('NA', 'PENDING', 'READY', 'FAILED');

-- CreateEnum
CREATE TYPE "AssignmentStatus" AS ENUM ('GENERATING', 'FAILED', 'DRAFT', 'PUBLISHED', 'ARCHIVED');

-- CreateTable
CREATE TABLE "users" (
    "id" UUID NOT NULL,
    "username" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "password_hash" TEXT NOT NULL,
    "role" "UserRole" NOT NULL,
    "real_name" TEXT,
    "avatar_url" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "qa_collection_enabled" BOOLEAN NOT NULL DEFAULT true,
    "qa_collection_notice_accepted_at" TIMESTAMP(3),

    CONSTRAINT "users_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "courses" (
    "id" UUID NOT NULL,
    "teacher_id" UUID NOT NULL,
    "name" TEXT NOT NULL,
    "description" TEXT,
    "cover_image_url" TEXT,
    "status" "CourseStatus" NOT NULL DEFAULT 'DRAFT',
    "share_code" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,
    "is_deleted" BOOLEAN NOT NULL DEFAULT false,

    CONSTRAINT "courses_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "course_collaborators" (
    "id" UUID NOT NULL,
    "course_id" UUID NOT NULL,
    "teacher_id" UUID NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "course_collaborators_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "lessons" (
    "id" UUID NOT NULL,
    "course_id" UUID NOT NULL,
    "title" TEXT NOT NULL,
    "description" TEXT,
    "order_index" INTEGER NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,
    "is_deleted" BOOLEAN NOT NULL DEFAULT false,

    CONSTRAINT "lessons_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "course_enrollments" (
    "id" UUID NOT NULL,
    "course_id" UUID NOT NULL,
    "student_id" UUID NOT NULL,
    "enrolled_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "course_enrollments_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "materials" (
    "id" UUID NOT NULL,
    "course_id" UUID NOT NULL,
    "lesson_id" UUID,
    "original_filename" TEXT NOT NULL,
    "file_type" TEXT NOT NULL,
    "file_size" INTEGER NOT NULL,
    "minio_path" TEXT NOT NULL,
    "preview_pdf_status" "MaterialPreviewPdfStatus" NOT NULL DEFAULT 'NA',
    "status" "MaterialStatus" NOT NULL DEFAULT 'UPLOADED',
    "status_message" TEXT,
    "indexed_chunk_count" INTEGER NOT NULL DEFAULT 0,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,
    "is_deleted" BOOLEAN NOT NULL DEFAULT false,

    CONSTRAINT "materials_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "material_images" (
    "id" UUID NOT NULL,
    "material_id" UUID NOT NULL,
    "page_idx" INTEGER NOT NULL,
    "minio_url" TEXT NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "material_images_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "refresh_tokens" (
    "id" UUID NOT NULL,
    "user_id" UUID NOT NULL,
    "token_hash" TEXT NOT NULL,
    "expires_at" TIMESTAMP(3) NOT NULL,
    "revoked_at" TIMESTAMP(3),
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "refresh_tokens_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "course_chat_sessions" (
    "id" UUID NOT NULL,
    "course_id" UUID NOT NULL,
    "student_id" UUID NOT NULL,
    "agent_session_id" VARCHAR(255) NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "course_chat_sessions_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "qa_center_sessions" (
    "id" UUID NOT NULL,
    "student_id" UUID NOT NULL,
    "agent_session_id" VARCHAR(255) NOT NULL,
    "title" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,
    "deleted_at" TIMESTAMP(3),

    CONSTRAINT "qa_center_sessions_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "chat_thread_title_overrides" (
    "id" UUID NOT NULL,
    "student_id" UUID NOT NULL,
    "session_id" VARCHAR(255) NOT NULL,
    "title" TEXT NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "chat_thread_title_overrides_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "qa_logs" (
    "id" UUID NOT NULL,
    "course_id" UUID,
    "student_id" UUID NOT NULL,
    "lesson_id" UUID,
    "session_id" VARCHAR(255) NOT NULL,
    "question" TEXT NOT NULL,
    "question_tokens" INTEGER,
    "answer" TEXT,
    "answer_tokens" INTEGER,
    "total_tokens" INTEGER,
    "execution_time_ms" INTEGER NOT NULL,
    "model_used" VARCHAR(100) NOT NULL,
    "hit_chunks" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "hit_materials" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "hit_sources" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "tool_calls" JSONB DEFAULT '[]',
    "citations" JSONB DEFAULT '[]',
    "response_quality" SMALLINT,
    "is_helpful" BOOLEAN,
    "agent_feedback" TEXT,
    "metadata" JSONB,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted_at" TIMESTAMP(3),

    CONSTRAINT "qa_logs_pkey" PRIMARY KEY ("id")
);

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

-- CreateTable
CREATE TABLE "user_learning_profiles" (
    "id" UUID NOT NULL,
    "user_id" UUID NOT NULL,
    "profile" JSONB NOT NULL,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "user_learning_profiles_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "user_memory_facts" (
    "id" UUID NOT NULL,
    "user_id" UUID NOT NULL,
    "session_id" TEXT NOT NULL,
    "timestamp" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "category" TEXT NOT NULL,
    "content" TEXT NOT NULL,
    "confidence" DOUBLE PRECISION NOT NULL,
    "source_json" JSONB NOT NULL,
    "metadata" JSONB NOT NULL DEFAULT '{}',

    CONSTRAINT "user_memory_facts_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "user_memory_concepts" (
    "id" UUID NOT NULL,
    "user_id" UUID NOT NULL,
    "name" TEXT NOT NULL,
    "description" TEXT NOT NULL DEFAULT '',
    "mastery_level" DOUBLE PRECISION NOT NULL DEFAULT 0,
    "last_updated" TIMESTAMP(3) NOT NULL,
    "supporting_fact_ids" TEXT[],
    "related_concepts" TEXT[],
    "metadata" JSONB NOT NULL DEFAULT '{}',

    CONSTRAINT "user_memory_concepts_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "cron_jobs" (
    "id" VARCHAR(64) NOT NULL,
    "user_id" UUID,
    "prompt" TEXT NOT NULL,
    "schedule" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'active',
    "last_run_at" TIMESTAMP(3),
    "next_run_at" TIMESTAMP(3),
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "cron_jobs_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "cron_job_runs" (
    "id" UUID NOT NULL,
    "job_id" VARCHAR(64) NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'running',
    "output" TEXT,
    "tool_calls" JSONB DEFAULT '[]',
    "started_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "finished_at" TIMESTAMP(3),
    "error_message" TEXT,

    CONSTRAINT "cron_job_runs_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "users_username_key" ON "users"("username");

-- CreateIndex
CREATE UNIQUE INDEX "users_email_key" ON "users"("email");

-- CreateIndex
CREATE UNIQUE INDEX "courses_share_code_key" ON "courses"("share_code");

-- CreateIndex
CREATE INDEX "courses_teacher_id_idx" ON "courses"("teacher_id");

-- CreateIndex
CREATE INDEX "course_collaborators_teacher_id_idx" ON "course_collaborators"("teacher_id");

-- CreateIndex
CREATE UNIQUE INDEX "course_collaborators_course_id_teacher_id_key" ON "course_collaborators"("course_id", "teacher_id");

-- CreateIndex
CREATE INDEX "lessons_course_id_idx" ON "lessons"("course_id");

-- CreateIndex
CREATE INDEX "course_enrollments_student_id_idx" ON "course_enrollments"("student_id");

-- CreateIndex
CREATE UNIQUE INDEX "course_enrollments_course_id_student_id_key" ON "course_enrollments"("course_id", "student_id");

-- CreateIndex
CREATE INDEX "materials_course_id_status_idx" ON "materials"("course_id", "status");

-- CreateIndex
CREATE INDEX "material_images_material_id_idx" ON "material_images"("material_id");

-- CreateIndex
CREATE INDEX "refresh_tokens_user_id_idx" ON "refresh_tokens"("user_id");

-- CreateIndex
CREATE INDEX "refresh_tokens_token_hash_idx" ON "refresh_tokens"("token_hash");

-- CreateIndex
CREATE UNIQUE INDEX "course_chat_sessions_agent_session_id_key" ON "course_chat_sessions"("agent_session_id");

-- CreateIndex
CREATE INDEX "course_chat_sessions_student_id_idx" ON "course_chat_sessions"("student_id");

-- CreateIndex
CREATE UNIQUE INDEX "course_chat_sessions_course_id_student_id_key" ON "course_chat_sessions"("course_id", "student_id");

-- CreateIndex
CREATE UNIQUE INDEX "qa_center_sessions_agent_session_id_key" ON "qa_center_sessions"("agent_session_id");

-- CreateIndex
CREATE INDEX "qa_center_sessions_student_id_idx" ON "qa_center_sessions"("student_id");

-- CreateIndex
CREATE INDEX "chat_thread_title_overrides_student_id_idx" ON "chat_thread_title_overrides"("student_id");

-- CreateIndex
CREATE UNIQUE INDEX "chat_thread_title_overrides_student_id_session_id_key" ON "chat_thread_title_overrides"("student_id", "session_id");

-- CreateIndex
CREATE INDEX "qa_logs_course_id_created_at_idx" ON "qa_logs"("course_id", "created_at" DESC);

-- CreateIndex
CREATE INDEX "qa_logs_student_id_created_at_idx" ON "qa_logs"("student_id", "created_at" DESC);

-- CreateIndex
CREATE INDEX "qa_logs_session_id_idx" ON "qa_logs"("session_id");

-- CreateIndex
CREATE INDEX "assignments_course_id_status_idx" ON "assignments"("course_id", "status");

-- CreateIndex
CREATE INDEX "assignments_course_id_created_at_idx" ON "assignments"("course_id", "created_at" DESC);

-- CreateIndex
CREATE UNIQUE INDEX "user_learning_profiles_user_id_key" ON "user_learning_profiles"("user_id");

-- CreateIndex
CREATE INDEX "user_memory_facts_user_id_timestamp_idx" ON "user_memory_facts"("user_id", "timestamp");

-- CreateIndex
CREATE INDEX "user_memory_facts_user_id_session_id_idx" ON "user_memory_facts"("user_id", "session_id");

-- CreateIndex
CREATE INDEX "user_memory_concepts_user_id_idx" ON "user_memory_concepts"("user_id");

-- CreateIndex
CREATE UNIQUE INDEX "user_memory_concepts_user_id_name_key" ON "user_memory_concepts"("user_id", "name");

-- CreateIndex
CREATE INDEX "cron_jobs_status_next_run_at_idx" ON "cron_jobs"("status", "next_run_at");

-- CreateIndex
CREATE INDEX "cron_jobs_user_id_idx" ON "cron_jobs"("user_id");

-- CreateIndex
CREATE INDEX "cron_job_runs_job_id_started_at_idx" ON "cron_job_runs"("job_id", "started_at" DESC);

-- AddForeignKey
ALTER TABLE "courses" ADD CONSTRAINT "courses_teacher_id_fkey" FOREIGN KEY ("teacher_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "course_collaborators" ADD CONSTRAINT "course_collaborators_course_id_fkey" FOREIGN KEY ("course_id") REFERENCES "courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "course_collaborators" ADD CONSTRAINT "course_collaborators_teacher_id_fkey" FOREIGN KEY ("teacher_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "lessons" ADD CONSTRAINT "lessons_course_id_fkey" FOREIGN KEY ("course_id") REFERENCES "courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "course_enrollments" ADD CONSTRAINT "course_enrollments_course_id_fkey" FOREIGN KEY ("course_id") REFERENCES "courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "course_enrollments" ADD CONSTRAINT "course_enrollments_student_id_fkey" FOREIGN KEY ("student_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "materials" ADD CONSTRAINT "materials_course_id_fkey" FOREIGN KEY ("course_id") REFERENCES "courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "materials" ADD CONSTRAINT "materials_lesson_id_fkey" FOREIGN KEY ("lesson_id") REFERENCES "lessons"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "material_images" ADD CONSTRAINT "material_images_material_id_fkey" FOREIGN KEY ("material_id") REFERENCES "materials"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "refresh_tokens" ADD CONSTRAINT "refresh_tokens_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "course_chat_sessions" ADD CONSTRAINT "course_chat_sessions_course_id_fkey" FOREIGN KEY ("course_id") REFERENCES "courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "course_chat_sessions" ADD CONSTRAINT "course_chat_sessions_student_id_fkey" FOREIGN KEY ("student_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "qa_center_sessions" ADD CONSTRAINT "qa_center_sessions_student_id_fkey" FOREIGN KEY ("student_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "chat_thread_title_overrides" ADD CONSTRAINT "chat_thread_title_overrides_student_id_fkey" FOREIGN KEY ("student_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "qa_logs" ADD CONSTRAINT "qa_logs_course_id_fkey" FOREIGN KEY ("course_id") REFERENCES "courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "qa_logs" ADD CONSTRAINT "qa_logs_student_id_fkey" FOREIGN KEY ("student_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "qa_logs" ADD CONSTRAINT "qa_logs_lesson_id_fkey" FOREIGN KEY ("lesson_id") REFERENCES "lessons"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "assignments" ADD CONSTRAINT "assignments_course_id_fkey" FOREIGN KEY ("course_id") REFERENCES "courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "assignments" ADD CONSTRAINT "assignments_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "user_learning_profiles" ADD CONSTRAINT "user_learning_profiles_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "user_memory_facts" ADD CONSTRAINT "user_memory_facts_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "user_memory_concepts" ADD CONSTRAINT "user_memory_concepts_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "cron_jobs" ADD CONSTRAINT "cron_jobs_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "cron_job_runs" ADD CONSTRAINT "cron_job_runs_job_id_fkey" FOREIGN KEY ("job_id") REFERENCES "cron_jobs"("id") ON DELETE CASCADE ON UPDATE CASCADE;
