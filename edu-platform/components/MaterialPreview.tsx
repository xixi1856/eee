"use client";

import CourseMaterialViewer from "@/components/CourseMaterialViewer";

type Props = {
  courseId: string;
  materialId?: string;
  chunkId?: string;
  sourceLabel?: string;
};

/** @deprecated Prefer ``CourseMaterialViewer``; kept for imports that pass ``courseId``. */
export default function MaterialPreview({
  courseId,
  materialId,
  chunkId,
  sourceLabel,
}: Props) {
  return (
    <CourseMaterialViewer
      courseId={courseId}
      materialId={materialId ?? null}
      chunkId={chunkId}
      sourceLabel={sourceLabel}
    />
  );
}
