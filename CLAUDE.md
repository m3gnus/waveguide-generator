# Claude Configuration

## Commit Behavior
**Claude should commit all changes at the end of each session.** This ensures:
- Work is not lost between sessions
- Clear atomic commits per session
- Clean git history for tracking progress

When committing:
1. Stage all modified files and new files (except `.claude/` and other ephemeral dirs)
2. Create a descriptive commit message following the existing convention
3. One commit per session preferred

## Development Preferences
- No automatic code formatting or refactoring unless explicitly requested
- Preserve existing code patterns and conventions
- Focus on requested tasks, not over-engineering
