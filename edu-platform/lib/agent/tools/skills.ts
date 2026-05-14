/**
 * Skills tools: list_skills, view_skill
 * Reads from the skills/ directory (server-side filesystem).
 */

import * as path from "path";
import type { Tool } from "../types";
import { SkillsLoader } from "../skills-loader";

function _getLoader(): SkillsLoader {
  // skills/ is at project root, one level above edu-platform/
  const skillsDir = path.join(process.cwd(), "..", "skills");
  return new SkillsLoader(skillsDir);
}

export const listSkillsTool: Tool = {
  name: "list_skills",
  description: "列出当前所有可用的教学技能（name + description 索引）。",
  parameters: { type: "object", properties: {}, required: [] },
  async execute(): Promise<string> {
    const loader = _getLoader();
    const skills = loader.load();
    if (skills.length === 0) return "（暂无已注册技能）";
    const lines = skills.map(
      (s) => `- **${s.name}** v${s.version}: ${s.description || "（无描述）"}`,
    );
    return lines.join("\n");
  },
};

export const viewSkillTool: Tool = {
  name: "view_skill",
  description:
    "查看某个技能的完整 SKILL.md 内容。" +
    "当需要了解某个技能的详细教学指导时调用。",
  parameters: {
    type: "object",
    properties: {
      name: { type: "string", description: "技能名称（来自 list_skills）" },
    },
    required: ["name"],
  },
  async execute(args: Record<string, unknown>): Promise<string> {
    const name = typeof args.name === "string" ? args.name.trim() : "";
    if (!name) return JSON.stringify({ error: "缺少必要参数：name" });
    const loader = _getLoader();
    const body = loader.getBody(name);
    if (body === null) return JSON.stringify({ error: `技能 "${name}" 不存在` });
    return body.slice(0, 8000);
  },
};
