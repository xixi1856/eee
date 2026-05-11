-- AlterTable
ALTER TABLE "courses" ADD COLUMN "share_code" TEXT;

-- CreateIndex
CREATE UNIQUE INDEX "courses_share_code_key" ON "courses"("share_code");

-- CreateTable
CREATE TABLE "course_collaborators" (
    "id" UUID NOT NULL,
    "course_id" UUID NOT NULL,
    "teacher_id" UUID NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "course_collaborators_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "course_collaborators_course_id_teacher_id_key" ON "course_collaborators"("course_id", "teacher_id");

-- CreateIndex
CREATE INDEX "course_collaborators_teacher_id_idx" ON "course_collaborators"("teacher_id");

-- AddForeignKey
ALTER TABLE "course_collaborators" ADD CONSTRAINT "course_collaborators_course_id_fkey" FOREIGN KEY ("course_id") REFERENCES "courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "course_collaborators" ADD CONSTRAINT "course_collaborators_teacher_id_fkey" FOREIGN KEY ("teacher_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;
