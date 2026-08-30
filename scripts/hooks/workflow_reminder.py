import json

def main():
    print(json.dumps({
        "injectSteps": [
            {
                "ephemeralMessage": "CRITICAL REMINDER: You are working in a highly documented environment. You MUST consult `AGENTS.md` and the specific house rules it references (`python-conventions.md`, `strategy.md`, `glossary.md`, `issue-tracker.md`, `workflow-cheatsheet.md`) before taking domain-specific actions. Use established workflows/skills (e.g., `/ship`, `/pr-babysitter`). Do NOT fall back to generic defaults or raw CLI shortcuts when a documented house rule exists. Consult with yourself on whether an action requires reading the docs first—if in doubt, read them."
            }
        ]
    }))

if __name__ == "__main__":
    main()
