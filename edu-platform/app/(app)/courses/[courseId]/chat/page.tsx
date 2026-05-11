"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ChevronLeft } from "lucide-react";
import CourseChatWorkspace from "@/components/CourseChatWorkspace";

export default function CourseChatPage() {
  const params = useParams();
  const courseId = typeof params?.courseId === "string" ? params.courseId : null;

  if (!courseId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        无效的课程链接
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between border-b border-border bg-background/80 backdrop-blur-sm px-4 py-3 shrink-0">
        <Link
          href={`/courses/${courseId}`}
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ChevronLeft size={15} />
          返回课程
        </Link>
        <h2 className="font-display text-sm font-semibold text-foreground">课程问答</h2>
        <div className="w-20" />
      </div>

      <CourseChatWorkspace courseId={courseId} />
    </div>
  );
}
