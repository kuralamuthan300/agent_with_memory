"""Action is the simplest role. It receives a ToolCall and a live MCP session,
dispatches the call, and returns a tuple of (descriptor, artifact_id_or_None).

Contains no LLM call. The full logic is roughly thirty lines.
"""

from pathlib import Path

from mcp.client.session import ClientSession

from artifacts import ArtifactStore
from schema import ToolCall

ARTIFACT_THRESHOLD_BYTES = 4096

_store = ArtifactStore(Path(__file__).parent / "state" / "artifacts")


async def execute(
    session: ClientSession,
    tool_call: ToolCall,
) -> tuple[str, str | None]:
    """Dispatch a ToolCall through the live MCP session.

    Three behaviours:

    1.  If *tool_call.arguments* contains a *path* or *url* value that starts
        with ``art:``, refuse the call and return an error string so the
        history records the problem.

    2.  Otherwise, call ``session.call_tool(...)``, collapse the result's
        content blocks into a single text string.

    3.  If the payload is larger than *ARTIFACT_THRESHOLD_BYTES* (4 KB),
        persist it via ``ArtifactStore.put(...)`` and return a short artifact
        descriptor.  Otherwise return the text directly.
    """
    print(f"  [ACTION] Guard check: validating arguments for artifact handles...")
    # ── Guard: reject artifact handles passed as path / url values ──
    for key in ("path", "url"):
        val = tool_call.arguments.get(key)
        if isinstance(val, str) and val.startswith("art:"):
            print(f"  [ACTION] REJECTED: argument '{key}' contains artifact handle '{val}'")
            return (
                f"ERROR: argument '{key}' has value '{val}' which looks like "
                f"an artifact handle, not a real path or URL.  Use the "
                f"actual file path or URL instead of an artifact id.",
                None,
            )

    print(f"  [ACTION] Dispatching tool \"{tool_call.name}\" with args: {tool_call.arguments}")
    # ── Real MCP dispatch ──
    result = await session.call_tool(tool_call.name, arguments=tool_call.arguments)
    text = _collapse_content(result.content)

    # ── Threshold check → persist large payloads ──
    payload_bytes = text.encode("utf-8")
    print(f"  [ACTION] Tool returned {len(payload_bytes)} bytes")
    if len(payload_bytes) > ARTIFACT_THRESHOLD_BYTES:
        print(f"  [ACTION] Payload exceeds threshold ({ARTIFACT_THRESHOLD_BYTES} bytes). Storing as artifact...")
        aid = _store.put(
            blob=payload_bytes,
            content_type="text/plain",
            source=f"tool:{tool_call.name}",
            descriptor=f"output of {tool_call.name}",
        )
        preview = text[:200].replace("\n", " ")
        descriptor = (
            f"[artifact {aid}, {len(payload_bytes)} bytes] preview: {preview}"
        )
        print(f"  [ACTION] Stored as artifact: {aid}")
        return descriptor, aid

    print(f"  [ACTION] Small payload, returning directly. Preview: {text[:100]}...")
    return text, None


def _collapse_content(content_blocks: list) -> str:
    """Collapse MCP ``CallToolResult.content`` blocks into a single string."""
    parts: list[str] = []
    for block in content_blocks:
        if hasattr(block, "text"):
            parts.append(block.text)
        elif isinstance(block, dict):
            parts.append(block.get("text", str(block)))
        else:
            parts.append(str(block))
    return "\n".join(parts)


if __name__ == "__main__":
    import asyncio
    import sys
    from unittest.mock import AsyncMock, MagicMock

    async def _test() -> int:
        errors = 0

        # ── 1. Test guard: path with art: handle ──
        tc = ToolCall(name="read_file", arguments={"path": "art:a1b2c3d4e5f6"})
        desc, aid = await execute(MagicMock(), tc)
        assert desc.startswith("ERROR:"), f"Expected ERROR prefix, got: {desc}"
        assert aid is None
        print("  ✓ guard: art: handle in path rejected")

        # ── 2. Test guard: url with art: handle ──
        tc = ToolCall(name="fetch_url", arguments={"url": "art:a1b2c3d4e5f6"})
        desc, aid = await execute(MagicMock(), tc)
        assert desc.startswith("ERROR:"), f"Expected ERROR prefix, got: {desc}"
        assert aid is None
        print("  ✓ guard: art: handle in url rejected")

        # ── 3. Test guard: non-art: values pass through to call_tool ──
        mock_session = AsyncMock()
        mock_session.call_tool.return_value = MagicMock(content=[])
        tc = ToolCall(name="get_time", arguments={"timezone": "UTC"})
        await execute(mock_session, tc)
        mock_session.call_tool.assert_awaited_once_with("get_time", arguments=tc.arguments)
        print("  ✓ guard: normal values pass through to dispatch")

        # ── 4. Test _collapse_content with TextContent-like objects ──
        class FakeText:
            def __init__(self, text: str):
                self.text = text

        blocks = [FakeText("hello"), FakeText("world")]
        collapsed = _collapse_content(blocks)
        assert collapsed == "hello\nworld", f"Expected 'hello\\nworld', got: {collapsed!r}"
        print("  ✓ _collapse_content: TextContent objects")

        # ── 5. Test _collapse_content with dicts ──
        blocks = [{"text": "foo"}, {"text": "bar"}]
        collapsed = _collapse_content(blocks)
        assert collapsed == "foo\nbar"
        print("  ✓ _collapse_content: dict blocks")

        # ── 6. Test _collapse_content with mixed types ──
        blocks = [FakeText("first"), {"text": "second"}, 42]
        collapsed = _collapse_content(blocks)
        assert "first\nsecond\n42" in collapsed
        print("  ✓ _collapse_content: mixed types")

        # ── 7. Test small payload returns text directly ──
        mock_session = AsyncMock()
        mock_session.call_tool.return_value = MagicMock(
            content=[FakeText("small payload")]
        )
        tc = ToolCall(name="get_time", arguments={"timezone": "UTC"})
        desc, aid = await execute(mock_session, tc)
        assert desc == "small payload", f"Expected 'small payload', got: {desc!r}"
        assert aid is None
        print("  ✓ threshold: small payload returned directly")

        # ── 8. Test large payload creates artifact ──
        big_text = "x" * (ARTIFACT_THRESHOLD_BYTES + 1)
        mock_session = AsyncMock()
        mock_session.call_tool.return_value = MagicMock(
            content=[FakeText(big_text)]
        )
        tc = ToolCall(name="list_dir", arguments={"path": "."})
        desc, aid = await execute(mock_session, tc)
        assert aid is not None, "Expected artifact ID for large payload"
        assert aid.startswith("art:"), f"Expected art: prefix, got: {aid}"
        assert "bytes]" in desc, f"Expected byte count in descriptor, got: {desc}"
        print(f"  ✓ threshold: large payload stored as artifact {aid}")

        return errors

    result = asyncio.run(_test())
    if result:
        print(f"\n❌ {result} test(s) failed")
    else:
        print("\n✅ All tests passed")
    sys.exit(result)
