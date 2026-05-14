import type { Tool, OpenAITool } from "./types";

export class ToolRegistry {
  private _tools = new Map<string, Tool>();

  register(tool: Tool): void {
    this._tools.set(tool.name, tool);
  }

  get(name: string): Tool | undefined {
    return this._tools.get(name);
  }

  getAll(): Tool[] {
    return [...this._tools.values()];
  }

  getSchemas(): OpenAITool[] {
    return this.getAll().map((t) => ({
      type: "function" as const,
      function: {
        name: t.name,
        description: t.description,
        parameters: t.parameters,
      },
    }));
  }
}
