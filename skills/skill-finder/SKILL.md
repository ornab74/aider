---
name: skill-finder
description: Locate the best SKILL.md and summarize only what the current task needs.
tags: [skills, context, discovery, summarize]
---
# Workflow
Parse `@skill:name` or `[skill:name]` first. Otherwise rank skills by name, tags, description, and body overlap with the task.

# Context control
Return the skill name, path, safety constraints, workflow, and only the most relevant command examples. Do not inject every skill into model context.

# Fallback
When no skill matches, continue with general safe defaults and propose a draft skill after a successful repeated workflow.
