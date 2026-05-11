import type {
  MaterialPreviewPdfStatus,
  MaterialStatus,
} from "@prisma/client";

export type MaterialSummaryDto = {
  id: string;
  filename: string;
  file_type: string;
  lesson_id: string | null;
  status: MaterialStatus;
  preview_pdf_status: MaterialPreviewPdfStatus;
  indexed_chunk_count: number;
  created_at: string;
  status_message: string | null;
};

export type MaterialDetailDto = {
  id: string;
  filename: string;
  file_type: string;
  lesson_id: string | null;
  status: MaterialStatus;
  preview_pdf_status: MaterialPreviewPdfStatus;
  indexed_chunk_count: number;
  created_at: string;
  status_message: string | null;
};

export type MaterialCreatedDto = {
  id: string;
  original_filename: string;
  status: MaterialStatus;
  created_at: string;
};
