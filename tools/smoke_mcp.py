"""End-to-end check of the MCP server over stdio, as a real client sees it.

    uv run python tools/smoke_mcp.py             # manual tools only, no Blender needed
    uv run python tools/smoke_mcp.py --blender   # also exercise the live Blender tools

Exits 0 when every check passed, 1 otherwise, and prints one line per check.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# The model-facing surface. Viewport app tools are filtered out for clients
# without MCP Apps support, which is what a bare stdio client is.
EXPECTED_TOOLS = {
    "bpy_api_lookup",
    "execute_blender_code",
    "generate_3d",
    "get_addon_status",
    "get_manual_page",
    "get_scene_info",
    "import_asset",
    "look",
    "search_assets",
    "search_manual",
}


class Checks:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        mark = "ok  " if ok else "FAIL"
        print(f"[{mark}] {name}{(' -- ' + detail) if detail else ''}")
        if not ok:
            self.failures += 1


async def call(session: ClientSession, tool: str, arguments: dict) -> str:
    result = await session.call_tool(tool, arguments)
    return "\n".join(part.text for part in result.content if getattr(part, "type", "") == "text")


async def run(command: str, exercise_blender: bool) -> int:
    checks = Checks()
    async with stdio_client(StdioServerParameters(command=command, args=[])) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            info = getattr(init, "server_info", None) or getattr(init, "serverInfo")
            print(f"server: {info.name} {info.version}")

            tools = {tool.name for tool in (await session.list_tools()).tools}
            print(f"tools: {len(tools)}")
            missing = EXPECTED_TOOLS - tools
            checks.check("model-facing tool set", not missing, f"missing {sorted(missing)}" if missing else "")

            text = await call(session, "search_manual", {"query": "array modifier", "limit": 3})
            checks.check("search_manual returns ranked pages", "manual pages" in text and "array" in text.lower())

            text = await call(session, "get_manual_page", {"path": "modeling/modifiers/generate/array", "section": "Options"})
            checks.check("get_manual_page returns a section", text.startswith("# Array Modifier") and "## Options" in text)

            if exercise_blender:
                text = await call(session, "get_addon_status", {})
                checks.check("get_addon_status reaches Blender", '"blender_version"' in text, text[:120])

                text = await call(session, "bpy_api_lookup", {"query": "ShaderNodeTexSky.sky_type"})
                checks.check("bpy_api_lookup returns enum identifiers", "enum_items" in text and "PREETHAM" in text)

    if checks.failures:
        print(f"\n{checks.failures} check(s) failed")
        return 1
    print("\nall checks passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the MCP server over stdio.")
    parser.add_argument(
        "--command",
        default="mcp-for-blender",
        help="server executable to launch (default: mcp-for-blender from PATH)",
    )
    parser.add_argument(
        "--blender",
        action="store_true",
        help="also call the tools that need a running Blender addon",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.command, args.blender))
    except Exception as exc:  # noqa: BLE001 - a smoke test reports, it does not raise
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
