"use client";

import { useState } from "react";
import { Network, RefreshCw, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";

type Status = "idle" | "loading" | "ready" | "empty";

interface KnowledgeGraphPanelProps {
  courseId: string;
  isTeacher: boolean;
}

export default function KnowledgeGraphPanel({ courseId, isTeacher }: KnowledgeGraphPanelProps) {
  const [status, setStatus] = useState<Status>("idle");

  const graphUrl = `/api/v1/courses/${courseId}/knowledge-graph-html`;

  async function handleLoad() {
    setStatus("loading");
    try {
      const res = await fetch(graphUrl, { credentials: "include" });
      if (res.status === 204 || !res.ok) {
        setStatus("empty");
      } else {
        setStatus("ready");
      }
    } catch {
      setStatus("empty");
    }
  }

  if (status === "idle") {
    if (!isTeacher) return null;
    return (
      <div className="rounded-xl border border-border bg-card p-5 flex items-center justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-foreground">知识图谱</p>
          <p className="text-xs text-muted-foreground mt-0.5">可视化本课程的实体关系网络</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void handleLoad()} className="shrink-0">
          <Network size={14} className="mr-1.5" />
          生成图谱
        </Button>
      </div>
    );
  }

  if (status === "loading") {
    return (
      <div className="rounded-xl border border-border bg-card p-8 flex items-center justify-center gap-2 text-muted-foreground text-sm">
        <Loader2 size={16} className="animate-spin" />
        加载知识图谱中…
      </div>
    );
  }

  if (status === "empty") {
    return (
      <div className="rounded-xl border border-border bg-card p-5">
        <div className="flex items-center justify-between mb-2">
          <p className="text-sm font-medium text-foreground">知识图谱</p>
          {isTeacher && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void handleLoad()}
              className="h-7 text-xs"
            >
              <RefreshCw size={12} className="mr-1" />
              重试
            </Button>
          )}
        </div>
        <p className="text-xs text-muted-foreground">暂无知识图谱数据，请先上传并索引课程资料。</p>
      </div>
    );
  }

  /* status === "ready" */
  return (
    <div className="rounded-xl border border-border overflow-hidden bg-card">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
        <p className="text-sm font-medium text-foreground flex items-center gap-1.5">
          <Network size={14} className="text-primary" />
          知识图谱
        </p>
        {isTeacher && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void handleLoad()}
            className="h-7 text-xs"
          >
            <RefreshCw size={12} className="mr-1" />
            刷新
          </Button>
        )}
      </div>
      <iframe
        src={graphUrl}
        className="w-full"
        style={{ height: "480px", border: "none" }}
        sandbox="allow-scripts"
        title="知识图谱"
      />
    </div>
  );
}
