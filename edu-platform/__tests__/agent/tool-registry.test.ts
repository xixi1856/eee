import { describe, it, expect } from "vitest";
import { ToolRegistry } from "@/lib/agent/tool-registry";
import type { Tool } from "@/lib/agent/types";

function makeTool(name: string, description: string): Tool {
  return {
    name,
    description,
    parameters: { type: "object", properties: {} },
    execute: async () => "ok",
  };
}

describe("ToolRegistry", () => {
  it("业务规则：注册后应可按名称获取工具", () => {
    // given
    const registry = new ToolRegistry();
    const tool = makeTool("knowledge_query", "查询知识库");

    // when
    registry.register(tool);
    const found = registry.get("knowledge_query");

    // then
    expect(found?.name).toBe("knowledge_query");
    expect(found?.description).toBe("查询知识库");
  });

  it("业务规则：同名工具后注册应覆盖前注册", () => {
    // given
    const registry = new ToolRegistry();
    registry.register(makeTool("search", "旧描述"));

    // when
    registry.register(makeTool("search", "新描述"));
    const schemas = registry.getSchemas();

    // then
    expect(schemas).toHaveLength(1);
    expect(schemas[0]?.function.name).toBe("search");
    expect(schemas[0]?.function.description).toBe("新描述");
  });

  it("业务规则：导出 schema 时应保留工具契约字段", () => {
    // given
    const registry = new ToolRegistry();
    registry.register({
      name: "generate_quiz",
      description: "生成题目",
      parameters: {
        type: "object",
        properties: { count: { type: "integer" } },
        required: ["count"],
      },
      execute: async () => "[]",
    });

    // when
    const schemas = registry.getSchemas();

    // then
    expect(schemas[0]?.type).toBe("function");
    expect(schemas[0]?.function.parameters).toEqual({
      type: "object",
      properties: { count: { type: "integer" } },
      required: ["count"],
    });
  });
});
