"use client";

import { useCallback, useEffect, useState, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Send, User, Bot, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { useChatStream } from "@/lib/hooks/useChatStream";

type UserMe = {
  qa_collection_enabled?: boolean;
  qa_collection_notice_accepted_at?: string | null;
};

export default function ChatComponent({ courseId }: { courseId: string }) {
  const [input, setInput] = useState("");
  const { msgs, streaming, busy, citations, lastMeta, errorMsg, sendMessage } = useChatStream(courseId);
  const scrollRef = useRef<HTMLDivElement>(null);

  const loadUser = useCallback(async () => {
    const res = await fetch("/api/v1/user", { credentials: "include" });
    if (!res.ok) return;
    const u = (await res.json()) as UserMe;
    if (!u.qa_collection_notice_accepted_at) {
      if (window.confirm("为改进教学，我们会记录提问数据。此行为可随时在个人资料关闭。确认知悉？")) {
        await fetch("/api/v1/user", {
          method: "PUT",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ qa_collection_notice_accepted: true }),
        });
      } else {
        await fetch("/api/v1/user", {
          method: "PUT",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ qa_collection_enabled: false }),
        });
      }
    }
  }, []);

  useEffect(() => {
    void loadUser();
  }, [loadUser]);

  useEffect(() => {
    if (scrollRef.current) {
      const scrollElement = scrollRef.current.querySelector('[data-radix-scroll-area-viewport]');
      if (scrollElement) {
        scrollElement.scrollTop = scrollElement.scrollHeight;
      }
    }
  }, [msgs, streaming]);

  const handleSend = () => {
    if (!input.trim() || busy) return;
    sendMessage(input);
    setInput("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex flex-col h-full bg-background relative">
      <ScrollArea ref={scrollRef} className="flex-1 w-full px-4 md:px-8 pt-6 pb-32">
        <div className="max-w-3xl mx-auto space-y-8 flex flex-col">
          {msgs.length === 0 && !streaming && (
            <div className="h-[50vh] flex flex-col items-center justify-center text-muted-foreground opacity-50">
              <Bot className="h-16 w-16 mb-4" />
              <p>向我提问关于本节课的任何问题</p>
            </div>
          )}

          {msgs.map((msg, i) => (
            <div
              key={i}
              className={cn(
                "flex w-full gap-4",
                msg.role === "user" ? "flex-row-reverse" : "flex-row"
              )}
            >
              <Avatar className={cn("w-8 h-8", msg.role === "user" ? "bg-muted" : "bg-primary text-primary-foreground")}>
                <AvatarFallback>{msg.role === "user" ? <User size={18}/> : <Bot size={18}/>}</AvatarFallback>
              </Avatar>
              <div
                className={cn(
                  "flex flex-col max-w-[80%]",
                  msg.role === "user" ? "items-end" : "items-start"
                )}
              >
                <div
                  className={cn(
                    "px-4 py-3 rounded-2xl",
                    msg.role === "user"
                      ? "bg-muted text-foreground rounded-tr-sm"
                      : "bg-transparent text-foreground prose prose-sm dark:prose-invert"
                  )}
                >
                  {msg.role === "user" ? (
                    <div className="whitespace-pre-wrap">{msg.text}</div>
                  ) : (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {msg.text}
                    </ReactMarkdown>
                  )}
                </div>
              </div>
            </div>
          ))}

          {streaming && (
            <div className="flex w-full gap-4 flex-row">
              <Avatar className="w-8 h-8 bg-primary text-primary-foreground">
                <AvatarFallback><Bot size={18}/></AvatarFallback>
              </Avatar>
              <div className="flex flex-col max-w-[80%] items-start">
                <div className="px-4 py-3 bg-transparent text-foreground prose prose-sm dark:prose-invert">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{streaming}</ReactMarkdown>
                  <span className="inline-block w-2 h-4 bg-primary animate-pulse ml-1 align-middle"></span>
                </div>
              </div>
            </div>
          )}

          {(citations.length > 0 || lastMeta) && (
            <div className="flex w-full justify-start pl-12">
              <div className="space-y-1.5">
                {citations.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {citations.map((c, ci) => (
                      <button
                        key={ci}
                        onClick={() => {
                          window.dispatchEvent(new CustomEvent("edu:open-material-preview", {
                            detail: {
                              materialId: c.material_id,
                              chunkId: c.chunk_id,
                              sourceLabel: c.source_label ?? `引用 ${ci + 1}`,
                            },
                          }));
                        }}
                        className="inline-flex items-center gap-1 rounded-full border border-primary/25 bg-primary/8 px-2.5 py-0.5 text-[11px] font-medium text-primary hover:bg-primary/15 transition-colors"
                      >
                        <span className="font-mono font-bold opacity-60">[{ci + 1}]</span>
                        <span className="max-w-[140px] truncate">{c.source_label ?? `引用 ${ci + 1}`}</span>
                      </button>
                    ))}
                  </div>
                )}
                {lastMeta?.type === "done" && !lastMeta.error && lastMeta.exec_time_ms && (
                  <p className="text-[10px] text-muted-foreground/60">{lastMeta.exec_time_ms} ms</p>
                )}
              </div>
            </div>
          )}
          
          {errorMsg && (
             <div className="flex w-full justify-center">
                <div className="flex items-center gap-2 text-xs text-destructive bg-destructive/10 px-3 py-1.5 rounded-full">
                  <AlertCircle size={14} />
                  <span>{errorMsg}</span>
                </div>
             </div>
          )}
        </div>
      </ScrollArea>

      <div className="absolute bottom-4 left-0 right-0 w-full px-4 md:px-8 bg-gradient-to-t from-background via-background to-transparent pt-6">
        <div className="max-w-3xl mx-auto relative rounded-2xl bg-muted/40 border border-border shadow-sm focus-within:ring-1 focus-within:ring-ring focus-within:bg-background transition-colors overflow-hidden">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="消息 Eduardo..."
            className="min-h-[52px] max-h-48 resize-none border-0 shadow-none bg-transparent py-4 text-sm focus-visible:ring-0"
            disabled={busy}
            rows={1}
          />
          <div className="absolute right-2 bottom-2">
            <Button
              size="icon"
              className={cn("h-8 w-8 rounded-full", !input.trim() || busy ? "opacity-50" : "")}
              onClick={handleSend}
              disabled={!input.trim() || busy}
            >
              <Send size={16} />
            </Button>
          </div>
        </div>
        <div className="text-center mt-2 text-xs text-muted-foreground">
          EduAgent 可能会犯错。请补充核实。
        </div>
      </div>
    </div>
  );
}
