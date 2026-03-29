import logging
import asyncio
import base64
from typing import Dict, Any, Callable
import httpx

logger = logging.getLogger(__name__)


class ScriptExecutor:
    """Execute generated Playwright scripts in sandbox."""

    def __init__(self, sandbox_url: str):
        self.sandbox_url = sandbox_url.rstrip("/")

    async def execute(
        self,
        session_id: str,
        script: str,
        on_log: Callable[[str], None] = None,
    ) -> Dict[str, Any]:
        """Execute script in sandbox and return output."""

        # Step 1: Write script to sandbox via sandbox_execute_code (base64 to avoid escaping)
        encoded = base64.b64encode(script.encode()).decode()
        write_code = (
            "import base64\n"
            f"data = base64.b64decode('{encoded}')\n"
            "with open('/tmp/rpa_test_script.py', 'wb') as f:\n"
            "    f.write(data)\n"
            "print('Script saved')"
        )
        await self._exec_code(session_id, write_code)
        if on_log:
            on_log("Script saved to sandbox")

        # Step 2: Kill any existing browser processes AND stop supervisord browser service
        # (supervisord has autorestart=true, so pkill alone won't work — it restarts immediately)
        await self._exec_cmd(
            session_id,
            "supervisorctl stop browser 2>/dev/null; "
            "supervisorctl stop mcp-server-browser 2>/dev/null; "
            "pkill -f rpa_browser.py 2>/dev/null; "
            "pkill -f chromium 2>/dev/null; "
            "sleep 1; echo 'Browsers cleaned'"
        )
        if on_log:
            on_log("Cleaned up existing browsers")

        # Step 3: Execute the script in background and poll for completion.
        # Running directly via sandbox_execute_bash can kill the process when
        # the bash command returns, so we use nohup + a sentinel file.
        if on_log:
            on_log("Executing script...")

        await self._exec_cmd(
            session_id,
            "rm -f /tmp/rpa_test_done.txt; "
            "nohup bash -c 'export DISPLAY=:99 && cd /tmp && "
            "python3 rpa_test_script.py > /tmp/rpa_test_output.txt 2>&1; "
            "echo DONE > /tmp/rpa_test_done.txt' &"
        )

        # Poll for completion (check every 2s, up to 90s)
        output = ""
        for _ in range(45):
            await asyncio.sleep(2)
            done = await self._exec_cmd(
                session_id,
                "cat /tmp/rpa_test_done.txt 2>/dev/null"
            )
            if "DONE" in done:
                output = await self._exec_cmd(
                    session_id,
                    "cat /tmp/rpa_test_output.txt 2>/dev/null"
                )
                break
        else:
            # Timeout — grab whatever output we have
            output = await self._exec_cmd(
                session_id,
                "cat /tmp/rpa_test_output.txt 2>/dev/null"
            )
            output += "\nTIMEOUT: Script did not complete within 90 seconds"

        if on_log:
            on_log(f"Execution output: {output[:500]}")

        # Step 4: Restart sandbox browser service
        await self._exec_cmd(
            session_id,
            "supervisorctl start browser 2>/dev/null; "
            "supervisorctl start mcp-server-browser 2>/dev/null; "
            "echo 'Browser restored'"
        )

        success = "SKILL_SUCCESS" in output and "SKILL_ERROR" not in output
        return {"success": success, "output": output}

    async def _exec_cmd(self, session_id: str, cmd: str) -> str:
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/call",
            "params": {"name": "sandbox_execute_bash", "arguments": {"cmd": cmd}},
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.sandbox_url}/mcp",
                json=payload,
                headers={
                    "X-Session-ID": session_id,
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
            resp.raise_for_status()
            result = resp.json()
            return result.get("result", {}).get("structuredContent", {}).get("output", "")

    async def _exec_code(self, session_id: str, code: str) -> str:
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/call",
            "params": {"name": "sandbox_execute_code", "arguments": {"code": code, "language": "python"}},
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.sandbox_url}/mcp",
                json=payload,
                headers={
                    "X-Session-ID": session_id,
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
            resp.raise_for_status()
            result = resp.json()
            sc = result.get("result", {}).get("structuredContent", {})
            return sc.get("stdout") or sc.get("output") or ""
