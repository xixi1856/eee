/**
 * Core types for the TS Agent (Phase 3B).
 */

// ---- Message ----------------------------------------------------------------

export type MessageRole = "system" | "user" | "assistant" | "tool";

export type Message = {
  role: MessageRole;
  content: string;
  /** Present when role === "tool" */
  tool_call_id?: string;
  /** Present when role === "assistant" and contains tool calls */
  tool_calls?: ToolCall[];
  name?: string;
};

export type ToolCall = {
  id: string;
  type: "function";
  function: {
    name: string;
    arguments: string; // JSON string
  };
};
// ---- Tool -------------------------------------------------------------------

export type JSONSchema = Record<string, unknown>;

/** Rich result from a tool that wants to emit citation events */
export type ToolCitation = {
  chunk_id?: string;
  material_id?: string;
  source_label?: string;
  chunk_text?: string;
  image_urls?: Array<{ page_idx: number; url: string }>;
};

export type ToolResult = {
  /** Text returned to the LLM */
  content: string;
  /** Optional citations emitted as SSE events */
  citations?: ToolCitation[];
};

export type ToolCategory = "read" | "write" | "external" | "dangerous";

export type Tool = {
  name: string;
  description: string;
  parameters: JSONSchema;
  /**
   * Execute the tool with the given parsed arguments.
   * Return a plain string or a ToolResult with optional citations.
   */
  execute: (args: Record<string, unknown>, ctx: TurnContext) => Promise<string | ToolResult>;
  /** Whether this tool requires explicit user approval before execution. */
  requiresApproval?: boolean;
  /** Human-readable reason shown to the user when requesting approval. */
  approvalReason?: string;
  /** Categorises the tool's side-effect level for UI display. */
  category?: ToolCategory;
};

export type OpenAITool = {
  type: "function";
  function: {
    name: string;
    description: string;
    parameters: JSONSchema;
  };
};

// ---- Context ----------------------------------------------------------------

export type TurnContext = {
  userId: string;
  sessionId: string;
  accessibleCourseIds: string[];
  courseId?: string | null;
  lessonId?: string | null;
  traceId?: string | null;
  debugTrace?: boolean;
};

// ---- Agent config -----------------------------------------------------------

export type AgentConfig = {
  model: string;
  systemPrompt: string;
  maxIterations: number;
  /** RAG Service base URL, e.g. http://localhost:8001 */
  ragServiceUrl: string;
  /** X-Internal-Key for RAG Service */
  ragServiceKey: string;
  /** Max context tokens before compression */
  maxContextTokens: number;
  /** Attachment URLs to include in the first user message */
  attachments?: Array<{
    id: string;
    presigned_url: string;
    mime_type: string;
    name: string;
  }>;
  /**
   * Controls approval behaviour for tools with requiresApproval=true.
   * - "require_user": pause the loop and wait for user confirmation (default for chat).
   * - "auto": skip approval and execute immediately (used by cron worker).
   */
  approvalMode?: "require_user" | "auto";
};

// ---- SSE output (B3 protocol) -----------------------------------------------

export type B3SseEvent =
  | { type: "text"; content: string }
  | { type: "citation"; chunk_id?: string; material_id?: string; source_label?: string; chunk_text?: string }
  | { type: "tool_call"; name: string; tool_call_id?: string }
  | { type: "tool_result"; name: string; success?: boolean; duration_ms?: number }
  | { type: "done"; tokens?: number | null; exec_time_ms?: number | null; error?: string }
  | { type: "trace"; trace_id?: string; event?: string; turn_id?: string; ts?: string; payload?: Record<string, unknown> }
  | {
      type: "require_approval";
      tool_call_id: string;
      tool_name: string;
      /** Sanitised preview of tool arguments (values truncated to 120 chars). */
      args_preview: Record<string, unknown>;
      /** Opaque Redis key the frontend must echo back to /api/v1/chat/approval */
      approval_key: string;
      reason: string;
    }
  | { type: "approval_resolved"; tool_call_id: string; approved: boolean };
