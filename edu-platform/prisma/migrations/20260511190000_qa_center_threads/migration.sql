-- QA center: global sessions, thread title overrides, nullable qa_logs.course_id

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

CREATE UNIQUE INDEX "qa_center_sessions_agent_session_id_key" ON "qa_center_sessions"("agent_session_id");

CREATE INDEX "qa_center_sessions_student_id_idx" ON "qa_center_sessions"("student_id");

ALTER TABLE "qa_center_sessions" ADD CONSTRAINT "qa_center_sessions_student_id_fkey" FOREIGN KEY ("student_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

CREATE TABLE "chat_thread_title_overrides" (
    "id" UUID NOT NULL,
    "student_id" UUID NOT NULL,
    "session_id" VARCHAR(255) NOT NULL,
    "title" TEXT NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "chat_thread_title_overrides_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "chat_thread_title_overrides_student_id_session_id_key" ON "chat_thread_title_overrides"("student_id", "session_id");

CREATE INDEX "chat_thread_title_overrides_student_id_idx" ON "chat_thread_title_overrides"("student_id");

ALTER TABLE "chat_thread_title_overrides" ADD CONSTRAINT "chat_thread_title_overrides_student_id_fkey" FOREIGN KEY ("student_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "qa_logs" ALTER COLUMN "course_id" DROP NOT NULL;
