-- B3: QA logs, course-scoped agent chat session, user collection consent

ALTER TABLE "users" ADD COLUMN "qa_collection_enabled" BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE "users" ADD COLUMN "qa_collection_notice_accepted_at" TIMESTAMP(3);

CREATE TABLE "course_chat_sessions" (
    "id" UUID NOT NULL,
    "course_id" UUID NOT NULL,
    "student_id" UUID NOT NULL,
    "agent_session_id" VARCHAR(255) NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "course_chat_sessions_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "course_chat_sessions_agent_session_id_key" ON "course_chat_sessions"("agent_session_id");

CREATE UNIQUE INDEX "course_chat_sessions_course_id_student_id_key" ON "course_chat_sessions"("course_id", "student_id");

CREATE INDEX "course_chat_sessions_student_id_idx" ON "course_chat_sessions"("student_id");

ALTER TABLE "course_chat_sessions" ADD CONSTRAINT "course_chat_sessions_course_id_fkey" FOREIGN KEY ("course_id") REFERENCES "courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "course_chat_sessions" ADD CONSTRAINT "course_chat_sessions_student_id_fkey" FOREIGN KEY ("student_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

CREATE TABLE "qa_logs" (
    "id" UUID NOT NULL,
    "course_id" UUID NOT NULL,
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
    "hit_chunks" TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    "hit_materials" TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    "hit_sources" TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    "response_quality" SMALLINT,
    "is_helpful" BOOLEAN,
    "agent_feedback" TEXT,
    "metadata" JSONB,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted_at" TIMESTAMP(3),

    CONSTRAINT "qa_logs_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "qa_logs_course_id_created_at_idx" ON "qa_logs"("course_id", "created_at" DESC);

CREATE INDEX "qa_logs_student_id_created_at_idx" ON "qa_logs"("student_id", "created_at" DESC);

CREATE INDEX "qa_logs_session_id_idx" ON "qa_logs"("session_id");

CREATE INDEX "qa_logs_hit_materials_idx" ON "qa_logs" USING GIN ("hit_materials");

ALTER TABLE "qa_logs" ADD CONSTRAINT "qa_logs_course_id_fkey" FOREIGN KEY ("course_id") REFERENCES "courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "qa_logs" ADD CONSTRAINT "qa_logs_student_id_fkey" FOREIGN KEY ("student_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "qa_logs" ADD CONSTRAINT "qa_logs_lesson_id_fkey" FOREIGN KEY ("lesson_id") REFERENCES "lessons"("id") ON DELETE SET NULL ON UPDATE CASCADE;
