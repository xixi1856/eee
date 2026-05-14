/**
 * Tool registry singleton instance — imported by both index.ts and delegation.ts
 * to avoid circular dependency.
 */
import { ToolRegistry } from "../tool-registry";

export const toolRegistry = new ToolRegistry();
