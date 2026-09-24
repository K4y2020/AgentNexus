#!/usr/bin/env python3
"""Migrate existing script nodes: move content from prompt to content field."""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import httpx


async def migrate_script_nodes():
    """Fix existing script nodes by moving prompt -> content."""

    seedance_base = "http://127.0.0.1:8893"
    api_key = os.environ.get("SEEDANCE_API_KEY", "")

    if not api_key:
        print("[FAIL] SEEDANCE_API_KEY not set")
        return

    headers = {"Accept": "application/json", "x-api-key": api_key}

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Target project
        project_id = "proj_99541a8a1b3d4c94b22bc6d2fe471448"
        print(f"[*] Migrating script nodes in project: {project_id}")
        print()

        # Get snapshot
        resp = await client.get(
            f"{seedance_base}/v3/projects/{project_id}/snapshot", headers=headers
        )

        snapshot = resp.json().get("snapshot", resp.json())
        nodes = snapshot.get("nodes", [])

        script_nodes = [n for n in nodes if n.get("type") == "script"]
        print(f"[*] Found {len(script_nodes)} script nodes")
        print()

        fixed_count = 0
        for node in script_nodes:
            node_id = node.get("id")
            title = node.get("title")
            revision = node.get("revision", 0)
            data = node.get("data", {})

            content = data.get("content", "")
            prompt = data.get("prompt", "")
            brief = data.get("brief", "")

            if not content and prompt:
                print(f"[FIX] {title[:60]}")
                print(f"      Moving {len(prompt)} chars from prompt to content")

                # Create update command
                command = {
                    "type": "canvas.update_node",
                    "nodeId": node_id,
                    "expectedRevision": revision,
                    "patch": {
                        "data": {
                            "content": prompt,
                            "brief": brief,
                            # Clear prompt field for script nodes
                            "prompt": "",
                        }
                    },
                    "commandId": f"migrate_{node_id}",
                }

                try:
                    resp = await client.post(
                        f"{seedance_base}/v3/projects/{project_id}/commands",
                        json={"command": command},
                        headers=headers,
                    )

                    result = resp.json()
                    if result.get("accepted"):
                        print(f"      [OK] Migration successful")
                        fixed_count += 1
                    else:
                        print(f"      [FAIL] {result}")
                except Exception as e:
                    print(f"      [ERROR] {e}")

                print()
            elif content:
                print(f"[SKIP] {title[:60]} - already has content")

        print("=" * 60)
        print(f"[DONE] Fixed {fixed_count} / {len(script_nodes)} script nodes")
        print("=" * 60)


if __name__ == "__main__":
    print("=" * 60)
    print("Script Node Migration Tool")
    print("=" * 60)
    print()
    print("This will move content from 'prompt' field to 'content' field")
    print("for all script-type nodes in the target project.")
    print()

    response = input("Continue? [y/N]: ")
    if response.lower() == "y":
        asyncio.run(migrate_script_nodes())
    else:
        print("Cancelled.")
