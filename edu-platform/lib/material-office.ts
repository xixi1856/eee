const OFFICE_FILE_TYPES = new Set(["ppt", "pptx", "doc", "docx"]);

export function isOfficeMaterialFileType(fileType: string): boolean {
  return OFFICE_FILE_TYPES.has(fileType.toLowerCase());
}

/** MinIO key for LibreOffice-generated preview PDF (sibling of original object). */
export function previewPdfObjectKey(originalMinioPath: string): string {
  const i = originalMinioPath.lastIndexOf("/");
  const parent = i >= 0 ? originalMinioPath.slice(0, i) : "";
  return parent ? `${parent}/preview.pdf` : "preview.pdf";
}

/** Legacy worker uploaded `{materialId}.pdf` before preview.pdf existed. */
export function legacyConvertedPdfObjectKey(
  originalMinioPath: string,
  materialId: string,
): string {
  const i = originalMinioPath.lastIndexOf("/");
  const parent = i >= 0 ? originalMinioPath.slice(0, i) : "";
  return parent ? `${parent}/${materialId}.pdf` : `${materialId}.pdf`;
}
