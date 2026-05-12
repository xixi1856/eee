"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ChevronLeft, LayoutGrid } from "lucide-react";
import CourseChatDockview from "@/components/CourseChatDockview";
import { Button } from "@/components/ui/button";

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
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-8 gap-1.5 text-xs shrink-0"
          title="若三栏都被关闭或布局异常，点此恢复资料列表、预览与问答"
          onClick={() =>
            window.dispatchEvent(
              new CustomEvent("edu:reset-course-chat-dockview", {
                detail: { courseId },
              }),
            )
          }
        >
          <LayoutGrid size={14} />
          恢复三栏
        </Button>
      </div>

      <CourseChatDockview courseId={courseId} />
    </div>
  );
}
