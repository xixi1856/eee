/**
 * SkillsLoader — reads skills/*.md (and skills/*‌/SKILL.md) files.
 * Mirrors Python's skills_loader.py.
 */

import * as fs from "fs";
import * as path from "path";

export type SkillEntry = {
  name: string;
  description: string;
  version: string;
  body: string;
  alwaysInject: boolean;
};

type Frontmatter = {
  name?: string;
  description?: string;
  version?: string;
  always_inject?: boolean;
};

function parseFrontmatter(raw: string): { meta: Frontmatter; body: string } {
  const match = raw.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  if (!match) return { meta: {}, body: raw };

  const yamlBlock = match[1];
  const body = match[2].trimStart();

  // Minimal YAML key: value parser (no arrays/objects needed for frontmatter)
  const meta: Frontmatter = {};
  for (const line of yamlBlock.split("\n")) {
    const kv = line.match(/^(\w+):\s*(.*)$/);
    if (!kv) continue;
    const [, key, val] = kv;
    const trimmed = val.trim();
    if (key === "name") meta.name = trimmed;
    else if (key === "description") meta.description = trimmed;
    else if (key === "version") meta.version = trimmed;
    else if (key === "always_inject") meta.always_inject = trimmed === "true";
  }
  return { meta, body };
}

export class SkillsLoader {
  private skillsDir: string;

  constructor(skillsDir: string) {
    this.skillsDir = skillsDir;
  }

  load(): SkillEntry[] {
    if (!fs.existsSync(this.skillsDir)) return [];

    const entries: SkillEntry[] = [];
    const seen = new Set<string>();

    // Directory-based skills (name/SKILL.md) take priority
    for (const entry of fs.readdirSync(this.skillsDir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const skillMd = path.join(this.skillsDir, entry.name, "SKILL.md");
      if (!fs.existsSync(skillMd)) continue;
      const raw = fs.readFileSync(skillMd, "utf-8");
      const { meta, body } = parseFrontmatter(raw);
      const name = meta.name ?? entry.name;
      seen.add(name);
      entries.push({
        name,
        description: meta.description ?? "",
        version: meta.version ?? "1.0.0",
        body,
        alwaysInject: meta.always_inject ?? false,
      });
    }

    // Flat skills/*.md files
    for (const entry of fs.readdirSync(this.skillsDir, { withFileTypes: true })) {
      if (!entry.isFile() || !entry.name.endsWith(".md")) continue;
      const stem = entry.name.replace(/\.md$/, "");
      const raw = fs.readFileSync(path.join(this.skillsDir, entry.name), "utf-8");
      const { meta, body } = parseFrontmatter(raw);
      const name = meta.name ?? stem;
      if (seen.has(name)) continue; // directory-based takes priority
      entries.push({
        name,
        description: meta.description ?? "",
        version: meta.version ?? "1.0.0",
        body,
        alwaysInject: meta.always_inject ?? false,
      });
    }

    return entries;
  }

  getBody(name: string): string | null {
    const all = this.load();
    return all.find((s) => s.name === name)?.body ?? null;
  }
}
