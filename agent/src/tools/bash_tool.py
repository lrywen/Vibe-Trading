"""Bash tool: execute shell commands under run_dir."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from src.agent.tools import BaseTool

_OUTPUT_LIMIT = 50_000
_DEFAULT_TIMEOUT = 120

# Get the agent source root directory for PYTHONPATH
_AGENT_SRC_ROOT = str(Path(__file__).resolve().parent.parent)


class BashTool(BaseTool):
    """Execute shell commands in the working directory."""

    name = "bash"
    description = "Execute a shell command in the working directory. Use for installing packages, running scripts, or inspecting files."
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
        },
        "required": ["command"],
    }
    repeatable = True
    is_readonly = False

    def execute(self, **kwargs: Any) -> str:
        """Execute a shell command.

        Args:
            **kwargs: Must include command. Optional run_dir used as cwd.

        Returns:
            JSON string with stdout, stderr, and exit_code.
        """
        command = kwargs["command"]
        cwd = kwargs.get("run_dir")

        # Build environment with proper PYTHONPATH for Python execution
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH", "")
        # Prepend agent src root to PYTHONPATH so `from src.xxx import` works
        if existing_pythonpath:
            env["PYTHONPATH"] = f"{_AGENT_SRC_ROOT}:{existing_pythonpath}"
        else:
            env["PYTHONPATH"] = _AGENT_SRC_ROOT

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=_DEFAULT_TIMEOUT,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            stdout = result.stdout[:_OUTPUT_LIMIT] if len(result.stdout) > _OUTPUT_LIMIT else result.stdout
            stderr = result.stderr[:_OUTPUT_LIMIT] if len(result.stderr) > _OUTPUT_LIMIT else result.stderr
            return json.dumps({
                "status": "ok" if result.returncode == 0 else "error",
                "exit_code": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
            }, ensure_ascii=False)
        except subprocess.TimeoutExpired:
            return json.dumps({
                "status": "error",
                "error": f"Command timed out after {_DEFAULT_TIMEOUT}s",
            }, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({
                "status": "error",
                "error": str(exc),
            }, ensure_ascii=False)
