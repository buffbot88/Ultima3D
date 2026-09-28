### YOU MUST ADHERE TO THE FOLLOWING PRACTICES FOR DEVELOPMENT
- Never rationalize for more than a quick moment without asking user.
- Never attempt shortcuts.
- Never hallucinate, assume, or decide without asking user for consent.
- Never create scaffolds/mock/boilerplates/AI Slop/or underbuild.
- Think rarely and on a budget: mechanical work (searches, lints, single-file edits, test runs) acts immediately with zero deliberation; anything warranting planning gets exactly ONE deliberate pass, and that pass must be visible as a written plan — never silent reasoning loops, repeated re-reads, or same-model re-derivations.
- Gather ALL context first, then plan with the best reasoning path available: start file-picker + code-searcher in parallel, read every file the change touches (symbols, current behavior, conventions, tests), and produce a solid build plan in the standard format (goal → files to touch with why → change list → risks → validation) — via a Thinker agent when one is available, otherwise by planning directly with an adversarial review before the plan reaches the user.
- Must ask the user if they approve of the build plan before implementing.
- Docstrings can be NO LONGER than 1 or 2 sentences.
- Always prepare project for a release push to GitHub Main for each major milestone completed.
