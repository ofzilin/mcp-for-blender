# Testing

Three levels: the automated suite, a live stdio check as a real MCP client sees it, and the
manual-corpus commands. This fork is maintained on `main` only, so run them after each change.

## 1. Automated suite

```bash
uv run --with pytest pytest -q
```

Expected: `610 passed, 1 skipped` (the count moves with new tests; the requirement is zero
failures). Run it from a full clone — `test_compat_matrix.py` reads past `addon.py` versions
with `git show`, so an sdist or shallow clone skips those cases.

| File | What it pins |
|---|---|
| `tests/test_consolidated_tools.py` | Exactly the ten model-facing tools are registered; app-only tools stay hidden from clients without MCP Apps |
| `tests/test_manual_docs.py` | RST parsing (headings, `.. include::`, code blocks, admonitions), BM25 ranking, section selection, truncation, missing corpus, installer |
| `tests/test_tool_schemas.py` | Every `= None` default also accepts an explicit null on the wire |
| `tests/test_openai_apps.py` | MCP Apps gating, viewport resources, picker round trip |
| `tests/test_safe_mode.py` | The `BLENDER_MCP_SAFE_MODE` AST allowlist |
| `tests/test_compat_matrix.py` | The current server against every published addon protocol version |
| `tests/test_addon_*.py`, `tests/test_polyhaven.py`, `test_sketchfab_status.py`, `test_polypizza.py`, `test_tripo.py`, `test_meshy.py`, `test_generation.py` | Addon handshake/update paths and the asset/generation providers |

## 2. Live stdio check

```bash
uv run python tools/smoke_mcp.py             # manual tools only, no Blender needed
uv run python tools/smoke_mcp.py --blender   # also the tools that talk to Blender
```

Expected: one `[ok  ]` line per check, then `all checks passed`, exit 0. It connects over stdio,
initializes, asserts the model-facing tool set, and calls `search_manual`, `get_manual_page`,
plus `get_addon_status` and `bpy_api_lookup` with `--blender`.

To check the installed binary rather than the source tree, pass `--command`:

```bash
uv run python tools/smoke_mcp.py --command "C:\Users\<user>\.local\bin\mcp-for-blender.exe" --blender
```

## 3. Manual corpus

```bash
mcp-for-blender manual                        # stats + usage
mcp-for-blender manual --search "array modifier"
mcp-for-blender manual --install <path-to-blender-manual/manual>
```

Expected stats: `2358 pages, 4.6M characters`, about 11k distinct terms. `search_manual` and
`get_manual_page` work with Blender closed; without a corpus they return installation
instructions and every other tool keeps working.

## Reinstalling after a change

```bash
git push origin main
uv tool install --force "git+https://github.com/ofzilin/mcp-for-blender"
uv run python tools/smoke_mcp.py --blender
```

`uv tool install` resolves the repository at install time only: a pushed change has no effect
until `--force` runs again. `uvx` / `uv tool run` re-resolve on every launch if you prefer that.

## Environment notes

- **Windows reinstall lock.** A running server holds
  `%APPDATA%\uv\tools\mcp-for-blender\Scripts`, so `uv tool install --force` fails with
  `拒绝访问 (os error 5)`. End the `mcp-for-blender.exe` process (or restart the MCP client)
  first, then reinstall; the client starts it again on the next session.
- **`github:owner/repo` is not uv syntax.** uv reads it as a relative path and fails with
  `os error 123`. Use `git+https://github.com/owner/repo` (append `@ref` to pin a branch, tag
  or commit).
- **No telemetry.** The telemetry stack is deleted, so `DISABLE_TELEMETRY` is unnecessary and
  can be dropped from client configs.
- **`--blender` needs Blender open** with the addon's *Start MCP Server* running on port 9876.
  The manual checks do not, and neither does the rest of the suite.
