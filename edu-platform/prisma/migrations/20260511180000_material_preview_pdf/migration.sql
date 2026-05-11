-- Preview PDF for Office materials (separate object; original minio_path unchanged).

CREATE TYPE "MaterialPreviewPdfStatus" AS ENUM ('NA', 'PENDING', 'READY', 'FAILED');

ALTER TABLE "materials" ADD COLUMN "preview_pdf_status" "MaterialPreviewPdfStatus" NOT NULL DEFAULT 'NA';

UPDATE "materials"
SET "preview_pdf_status" = 'PENDING'::"MaterialPreviewPdfStatus"
WHERE "file_type" IN ('ppt', 'pptx', 'doc', 'docx')
  AND "preview_pdf_status" = 'NA'::"MaterialPreviewPdfStatus"
  AND "status"::text NOT IN ('READY', 'FAILED')
  AND "is_deleted" = false;
