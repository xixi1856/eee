/**
 * Course material upload extensions. Keep in sync with RAG worker
 * (`parse_material` / `_OFFICE_SUFFIXES` in `src/rag_mvp/material_processor.py`).
 */
export const MATERIAL_UPLOAD_ALLOWED_EXTENSIONS = [
  "pdf",
  "md",
  "txt",
  "ppt",
  "pptx",
  "doc",
  "docx",
] as const;

export const MATERIAL_UPLOAD_ALLOWED_EXT_SET = new Set<string>(
  MATERIAL_UPLOAD_ALLOWED_EXTENSIONS,
);

export const MATERIAL_UPLOAD_ACCEPT = MATERIAL_UPLOAD_ALLOWED_EXTENSIONS.map(
  (e) => `.${e}`,
).join(",");

export function materialUploadAllowedLabel(): string {
  return MATERIAL_UPLOAD_ALLOWED_EXTENSIONS.join("、");
}
