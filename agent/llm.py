from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agent import config

try:
    import anthropic
except ImportError:
    anthropic = None


class LlmError(RuntimeError):
    pass


class ClaudeCli:
    """The local Claude Code CLI in headless mode: no API key, only a one-time `claude auth login`."""

    def __init__(self) -> None:
        self.executable = _find_cli()
        if self.executable is None:
            raise LlmError("Claude CLI introuvable : installez Claude Code ou fournissez une clé API Anthropic.")
        self.name = f"Claude CLI ({config.CLI_MODEL})"

    def ask(self, system: str, prompt: str, schema: dict[str, Any], images: list[Path]) -> dict[str, Any]:
        args = [
            str(self.executable), "-p", "--output-format", "json", "--model", config.CLI_MODEL,
            "--no-session-persistence", "--system-prompt", system, "--json-schema", json.dumps(schema),
            "--tools", "Read", "--allowedTools", "Read",
        ]
        if images:
            args += ["--add-dir", str(images[0].parent)]
            prompt += "\n\nImages des pages scannées, à lire avec l'outil Read :\n" + "\n".join(map(str, images))
        try:
            result = subprocess.run(args, input=prompt.encode("utf-8"), capture_output=True, env=_cli_env(),
                                    timeout=config.LLM_TIMEOUT_SECONDS, check=False)
            reply = json.loads(result.stdout)
        except subprocess.TimeoutExpired as error:
            raise LlmError("Claude CLI n'a pas répondu à temps.") from error
        except ValueError as error:
            stderr = result.stderr.decode(errors="replace")[:200]
            raise LlmError(f"Réponse illisible du Claude CLI : {stderr}") from error
        message = str(reply.get("result", ""))
        if reply.get("is_error"):
            if "authenticate" in message.lower():
                message = "Claude CLI non connecté : lancez `claude auth login` dans un terminal."
            raise LlmError(message)
        if reply.get("structured_output"):
            return reply["structured_output"]
        found = re.search(r"\{.*\}", message, re.DOTALL)
        if not found:
            raise LlmError("Le Claude CLI n'a pas renvoyé de JSON.")
        return json.loads(found.group())


class AnthropicApi:
    """Same extraction through the Anthropic API, used when an API key is provided."""

    def __init__(self, api_key: str) -> None:
        if anthropic is None:
            raise LlmError("Installez le paquet `anthropic` pour utiliser une clé API.")
        self._client = anthropic.Anthropic(api_key=api_key, timeout=config.LLM_TIMEOUT_SECONDS)
        self.name = f"API Anthropic ({config.API_MODEL})"

    def ask(self, system: str, prompt: str, schema: dict[str, Any], images: list[Path]) -> dict[str, Any]:
        content = [_image_block(image) for image in images] + [{"type": "text", "text": prompt}]
        try:
            response = self._client.beta.messages.create(
                model=config.API_MODEL,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_config={"effort": "high", "format": {"type": "json_schema", "schema": schema}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.APIError as error:
            raise LlmError(f"Erreur de l'API Anthropic : {error}") from error
        if response.stop_reason == "refusal":
            raise LlmError("Le modèle a refusé la requête.")
        return json.loads(next(block.text for block in response.content if block.type == "text"))


LlmClient = ClaudeCli | AnthropicApi


def make_client(api_key: str = "") -> LlmClient:
    key = api_key or config.ANTHROPIC_API_KEY
    return AnthropicApi(key) if key else ClaudeCli()


def _find_cli() -> Path | None:
    found = shutil.which("claude")
    if not found:
        return None
    native = Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    return native if native.exists() else Path(found)


def _cli_env() -> dict[str, str]:
    """Started from inside a Claude Code session, the CLI would borrow that session's login: drop those variables."""
    env = dict(os.environ)
    if "CLAUDECODE" in env:
        for key in [key for key in env if key.startswith("CLAUDE") or key == "ANTHROPIC_BASE_URL"]:
            del env[key]
    return env


def _image_block(path: Path) -> dict[str, Any]:
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}
