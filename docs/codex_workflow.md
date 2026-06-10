# Codex Workflow

## Roles

ChatGPT:

- project manager
- architect
- Codex prompt writer
- risk reviewer

Codex:

- coding worker
- file editor
- test runner

User:

- runs Codex
- runs local commands
- pastes Codex output back into ChatGPT

## Loop

1. ChatGPT gives a Codex prompt.
2. User pastes it into Codex.
3. Codex edits files and runs tests.
4. User pastes Codex result back into ChatGPT.
5. ChatGPT reviews and writes next prompt.

## What to paste back to ChatGPT

```text
1. Codex summary
2. Changed files
3. Test output
4. Errors
5. Questions Codex asked
```
