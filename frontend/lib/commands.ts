import type { AgentFamily } from "@/types";

// ==========================================
// @ Command Registry
// ==========================================

export interface AtCommand {
  /** Arabic trigger text without the @ prefix (e.g., "بحث_معمق") */
  trigger: string;
  /** Display label shown in the palette (e.g., "بحث معمق") */
  label: string;
  /** Arabic description of the command */
  description: string;
  /** Agent family to invoke when this command is used */
  agent_family?: AgentFamily;
  /** If true, this is a modifier (e.g., @خطة, @تأمل) rather than an agent selector */
  is_modifier?: boolean;
}

export const AT_COMMANDS: AtCommand[] = [
  {
    trigger: "بحث_معمق",
    label: "بحث معمق",
    description: "بحث قانوني معمق مع تقرير",
    agent_family: "deep_search",
  },
  {
    trigger: "بحث",
    label: "بحث",
    description: "بحث سريع في الأنظمة",
    agent_family: "deep_search",
  },
  {
    trigger: "عقد",
    label: "إنشاء عقد",
    description: "إنشاء مسودة عقد أو مستند",
    agent_family: "end_services",
  },
  {
    trigger: "استخراج",
    label: "استخراج معلومات",
    description: "استخراج بيانات من مستند",
    agent_family: "extraction",
  },
  {
    trigger: "ذاكرة",
    label: "ذاكرة القضية",
    description: "إدارة ذاكرة القضية",
    agent_family: "memory",
  },
  {
    trigger: "خطة",
    label: "وضع خطة",
    description: "التخطيط قبل التنفيذ",
    is_modifier: true,
  },
  {
    trigger: "تأمل",
    label: "تأمل",
    description: "مراجعة وتحليل",
    is_modifier: true,
  },
];

// ==========================================
// Parse Result
// ==========================================

export interface ParseResult {
  /** Cleaned message content with @ commands stripped */
  content: string;
  /** The selected agent family (first non-modifier command wins) */
  agent_family: AgentFamily | null;
  /** English modifier strings extracted from @خطة and @تأمل */
  modifiers: string[];
}

// ==========================================
// Modifier mapping (Arabic trigger -> English key)
// ==========================================

const MODIFIER_MAP: Record<string, string> = {
  "خطة": "plan",
  "تأمل": "reflect",
};

// ==========================================
// Parser
// ==========================================

/**
 * Parses @ commands from user input.
 * Extracts agent family + modifiers and returns cleaned content.
 *
 * Commands are matched longest-first to avoid partial matches
 * (e.g., "بحث_معمق" should match before "بحث").
 */
export function parseAtCommands(input: string): ParseResult {
  let content = input;
  let agent_family: AgentFamily | null = null;
  const modifiers: string[] = [];

  // Sort commands longest-trigger-first to avoid partial matches
  const sorted = [...AT_COMMANDS].sort(
    (a, b) => b.trigger.length - a.trigger.length
  );

  for (const cmd of sorted) {
    const pattern = `@${cmd.trigger}`;
    if (content.includes(pattern)) {
      content = content.replace(pattern, "").trim();
      if (cmd.is_modifier) {
        const englishKey = MODIFIER_MAP[cmd.trigger];
        if (englishKey && !modifiers.includes(englishKey)) {
          modifiers.push(englishKey);
        }
      } else if (cmd.agent_family && !agent_family) {
        agent_family = cmd.agent_family;
      }
    }
  }

  // Collapse multiple spaces left by stripping commands
  content = content.replace(/\s{2,}/g, " ").trim();

  return { content, agent_family, modifiers };
}

// ==========================================
// Filter (for autocomplete)
// ==========================================

/**
 * Filters AT_COMMANDS by a partial query string.
 * Returns all commands when query is empty.
 */
export function filterCommands(query: string): AtCommand[] {
  if (!query) return AT_COMMANDS;
  return AT_COMMANDS.filter(
    (cmd) => cmd.trigger.includes(query) || cmd.label.includes(query)
  );
}
