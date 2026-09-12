#!/usr/bin/env python3
"""Durably tolerate the DeepSeek image placeholder token in message text.

`encoding_dsv41._validate_no_image_sp_tokens` raises ValueError when a message's *text*
contains the literal image placeholder token. Any conversation whose transcript carries
that token -- a model-emitted token replayed from history, or a log file fed back as
context -- fails with HTTP 500 on the Anthropic endpoint (/v1/messages), and because the
token re-enters the transcript the failure is self-sustaining: every later request in that
conversation fails the same way. Measured by upstream PR #15 on a 4x DGX Spark TP4/EP2
fleet (same topology class as ours): 111/145 Anthropic-protocol requests failed while the
literal was present; the OpenAI endpoint is unaffected.

This script rewrites the guard to sanitize the token into plain text and log it, instead
of rejecting the request. Real image content blocks are untouched.

Idempotent: re-running on an already-patched file is a no-op. Exits non-zero if the
anchor is not found exactly once, so a base-image change cannot silently drop the fix.

Run at docker build time:  COPY runtime/patch_encoding_dsv41.py /opt/dsv41/runtime/
                           RUN python3 /opt/dsv41/runtime/patch_encoding_dsv41.py
Run standalone (already-built image): docker exec dsv41-head python3 /opt/dsv41/runtime/patch_encoding_dsv41.py
"""
import pathlib
import sys

TARGET = pathlib.Path(
    "/sgl-workspace/sglang/python/sglang/srt/entrypoints/openai/encoding_dsv41.py"
)
# Present in any revision of this fix, so re-running on an already-patched image is a no-op.
MARKER = "sanitizing image placeholder token"

OLD = '''def _validate_no_image_sp_tokens(msg: Dict[str, Any]) -> None:
    """Reject user-supplied image placeholder tokens in textual fields."""
    content = msg.get("content")
    if isinstance(content, str) and IMAGE_PLACEHOLDER in content:
        raise ValueError(
            f"Message content contains image special token '{IMAGE_PLACEHOLDER}'. "
            "Images should be provided as image content blocks."
        )
    reasoning_content = msg.get("reasoning_content")
    if isinstance(reasoning_content, str) and IMAGE_PLACEHOLDER in reasoning_content:
        raise ValueError(
            f"reasoning_content contains image special token '{IMAGE_PLACEHOLDER}'"
        )
'''

NEW = '''def _validate_no_image_sp_tokens(msg: Dict[str, Any]) -> None:
    """Sanitize textual image placeholder tokens instead of rejecting the request.

    Raising here turns any conversation whose text carries the literal placeholder into a
    hard HTTP 500, and because the token then re-enters the transcript the failure is
    self-sustaining. Measured on a 4x DGX Spark fleet: 111/145 Anthropic-protocol
    requests failed before this patch. Rewrite the placeholder into plain text so the
    request can proceed.
    """
    content = msg.get("content")
    if isinstance(content, str) and IMAGE_PLACEHOLDER in content:
        print("[encoding_dsv41] sanitizing image placeholder token in message content",
              flush=True)
        msg["content"] = content.replace(
            IMAGE_PLACEHOLDER, "[image omitted: placeholder token in text]"
        )
    reasoning_content = msg.get("reasoning_content")
    if isinstance(reasoning_content, str) and IMAGE_PLACEHOLDER in reasoning_content:
        print("[encoding_dsv41] sanitizing image placeholder token in reasoning_content",
              flush=True)
        msg["reasoning_content"] = reasoning_content.replace(
            IMAGE_PLACEHOLDER, "[image omitted: placeholder token in text]"
        )
'''


def main() -> int:
    if not TARGET.exists():
        print(f"[patch] FATAL: target not found: {TARGET}", flush=True)
        return 2
    text = TARGET.read_text()
    if MARKER in text:
        print("[patch] already patched; no-op", flush=True)
        return 0
    count = text.count(OLD)
    if count != 1:
        print(
            f"[patch] FATAL: anchor found {count} times (expected exactly 1). "
            "Base image drifted; refusing to guess. Update OLD to match the new source.",
            flush=True,
        )
        return 3
    TARGET.write_text(text.replace(OLD, NEW))
    # Verify the write landed and the module still parses.
    import ast

    ast.parse(TARGET.read_text())
    if MARKER not in TARGET.read_text():
        print("[patch] FATAL: patch applied but marker missing afterwards", flush=True)
        return 4
    print("[patch] OK: guard rewritten to sanitize; module parses", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
