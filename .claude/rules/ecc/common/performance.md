# Performance Optimization

## Context Window Management

Avoid last 20% of context window for:
- Large-scale refactoring
- Feature implementation spanning multiple files
- Debugging complex interactions

Lower context sensitivity tasks:
- Single-file edits
- Independent utility creation
- Documentation updates
- Simple bug fixes

## Build Troubleshooting

If build or tests fail:
1. Analyze error messages
2. Fix incrementally
3. Verify after each fix
4. Run `python -m pytest -q` to confirm green
