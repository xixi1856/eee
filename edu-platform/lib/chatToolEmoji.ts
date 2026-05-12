/** Mirrors ``src/edu_agent/cli.py`` ``_TOOL_EMOJIS`` for chat UI parity. */
const TOOL_EMOJIS: Record<string, string> = {
  knowledge_query: "🔍",
  generate_quiz: "📝",
  build_mindmap: "🗺️",
  parse_document: "📄",
  ingest_document: "📥",
  hint_generator: "💡",
  score_essay: "✅",
  evaluate_code: "💻",
  delegate_task: "🤝",
  wikipedia_search: "🌐",
  web_search: "🔎",
  web_fetch: "🌍",
  ollama_web_search: "🦙",
  write_file: "💾",
  read_file: "📂",
  list_skills: "📚",
  view_skill: "👁️",
  manage_skill: "🛠️",
  cron_job: "⏰",
};

export function toolEmoji(name: string): string {
  return TOOL_EMOJIS[name] ?? "⚡";
}
