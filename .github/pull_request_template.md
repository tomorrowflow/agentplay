## What and why

<!-- One change per PR. Link the issue it closes, if any. -->

## Checklist

- [ ] `make check` passes
- [ ] Tests added or updated
- [ ] No prompt text, model output, tool input/output, file contents or command arguments can reach a map, a log line or the viewer ([redaction contract](https://github.com/Entelligence-AI/agentplay/blob/main/docs/format.md#redaction-contract))
- [ ] No new network calls or Python runtime dependencies
- [ ] UI changes: before/after screenshots in light and dark (`pnpm screenshots` in `viewer/`)
