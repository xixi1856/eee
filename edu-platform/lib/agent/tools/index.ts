/**
 * Tool registry singleton — registers all tools for the TS Agent.
 */

export { toolRegistry } from "./registry";
import { toolRegistry } from "./registry";
import { knowledgeQueryTool, generateQuizTool, buildMindmapTool } from "./rag";
import { hintGeneratorTool, scoreEssayTool, evaluateCodeTool } from "./eval";
import { rememberFactTool, searchMemoryTool } from "./memory";
import { webSearchTool, wikipediaSearchTool } from "./search";
import { listSkillsTool, viewSkillTool } from "./skills";
import { parseDocumentTool } from "./ocr";
import { analyzeImageTool } from "./vision";

// RAG tools
toolRegistry.register(knowledgeQueryTool);
toolRegistry.register(generateQuizTool);
toolRegistry.register(buildMindmapTool);

// Eval tools
toolRegistry.register(hintGeneratorTool);
toolRegistry.register(scoreEssayTool);
toolRegistry.register(evaluateCodeTool);

// Memory tools
toolRegistry.register(rememberFactTool);
toolRegistry.register(searchMemoryTool);

// Search tools
toolRegistry.register(webSearchTool);
toolRegistry.register(wikipediaSearchTool);

// Skills tools
toolRegistry.register(listSkillsTool);
toolRegistry.register(viewSkillTool);

// OCR / document parsing
toolRegistry.register(parseDocumentTool);

// Vision tool — image understanding via dedicated vision model
toolRegistry.register(analyzeImageTool);

// delegation tool — imported after other tools to avoid circular import issue
import { delegateTaskTool } from "./delegation";
toolRegistry.register(delegateTaskTool);

export { knowledgeQueryTool, generateQuizTool, buildMindmapTool };
export { hintGeneratorTool, scoreEssayTool, evaluateCodeTool };
export { rememberFactTool, searchMemoryTool };
export { webSearchTool, wikipediaSearchTool };
export { listSkillsTool, viewSkillTool };
export { parseDocumentTool };
export { delegateTaskTool };
export { analyzeImageTool };
