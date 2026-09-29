import json, sys
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main(url, key):
    async with streamablehttp_client(url, headers={"X-API-Key": key}) as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("TOOLS:", sorted(t.name for t in tools.tools))
            res = await s.call_tool("browser_open", {"url": "https://example.com"})
            sid = json.loads(res.content[0].text)["session_id"]
            print("OPEN_OK sid=", sid)
            res2 = await s.call_tool("browser_snapshot", {"session_id": sid})
            snap = json.loads(res2.content[0].text)
            print("SNAPSHOT_OK title=", snap["title"], "chars=", len(snap["text"]))
            res3 = await s.call_tool("browser_screenshot", {"session_id": sid})
            print("SHOT_OK content_types=", [c.type for c in res3.content])
            await s.call_tool("browser_close", {"session_id": sid})
            print("MCP_SMOKE_OK")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main(sys.argv[1], sys.argv[2]))
