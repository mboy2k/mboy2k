"""Smoke test for the machine-as-a-service surface (MCP tools + REST).

Usage: .venv/bin/python tests/machine_smoke.py http://127.0.0.1:3000 <api-key>
"""

import json
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main(base: str, key: str) -> None:
    async with streamablehttp_client(f"{base}/mcp", headers={"X-API-Key": key}) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = sorted(t.name for t in (await session.list_tools()).tools)
            assert "machine_run" in tools and "machine_write_file" in tools, tools
            print("TOOLS_OK", len(tools), "tools")

            res = await session.call_tool("machine_run", {"cmd": "echo mcp-hello && pwd"})
            out = json.loads(res.content[0].text)
            assert out["code"] == 0 and "mcp-hello" in out["stdout"], out
            print("MCP_RUN_OK cwd=", out["cwd"])

            res = await session.call_tool("machine_write_file",
                                          {"path": "/tmp/mcp-smoke.txt", "content": "agent wrote this"})
            assert json.loads(res.content[0].text)["bytes"] > 0
            res = await session.call_tool("machine_read_file", {"path": "/tmp/mcp-smoke.txt"})
            assert json.loads(res.content[0].text)["content"] == "agent wrote this"
            res = await session.call_tool("machine_list_dir", {"path": "/tmp"})
            names = [e["name"] for e in json.loads(res.content[0].text)["entries"]]
            assert "mcp-smoke.txt" in names
            print("MCP_FILES_OK")

    async with httpx.AsyncClient(base_url=base, headers={"X-API-Key": key}, timeout=30) as client:
        assert (await client.get("/machine/info")).status_code == 200
        res = await client.post("/machine/exec", json={"cmd": "echo rest-hello"})
        assert "rest-hello" in res.json()["stdout"], res.text
        wrong = await client.post("/machine/exec", json={"cmd": "echo x"},
                                  headers={"X-API-Key": "wrong"})
        assert wrong.status_code == 401, wrong.status_code
        print("REST_OK")

    print("MACHINE_SMOKE_OK")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main(sys.argv[1], sys.argv[2]))
