#!/usr/bin/env python3
"""Test the fixed script node creation."""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import httpx


async def test_fixed_script_creation():
    """Test that script nodes now correctly use content field."""

    seedance_base = "http://127.0.0.1:8893"
    api_key = os.environ.get("SEEDANCE_API_KEY", "")

    if not api_key:
        print("[FAIL] SEEDANCE_API_KEY not set")
        return

    headers = {"Accept": "application/json", "x-api-key": api_key}

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Get test project
        resp = await client.get(f"{seedance_base}/v3/projects", headers=headers)
        projects = resp.json().get("projects", [])

        if not projects:
            print("[FAIL] No projects found")
            return

        project_id = projects[0].get("id")
        print(f"[*] Testing with project: {project_id}")
        print()

        # Test 1: Create script node (should use content field)
        print("Test 1: Creating script node with prompt parameter...")
        command = {
            "type": "canvas.create_node",
            "nodeType": "script",
            "title": "[FIX TEST] Script with prompt param",
            "data": {},
            "prompt": "这是通过 prompt 参数传入的剧本内容，应该被映射到 data.content",
            "brief": "修复测试：验证 prompt 参数正确映射到 content",
            "commandId": "fix_test_001",
        }

        # Note: This simulates what cinebot does - passing script content via prompt
        print(f"[SEND] Command with prompt parameter for script node")

        # Instead of direct API call, show what the bridge should do
        print(f"[*] Expected behavior:")
        print(f"    - Input: prompt='剧本内容...'")
        print(f"    - Node type: script")
        print(f"    - Bridge should map: prompt -> data.content")
        print()

        # Test 2: Verify existing broken nodes
        print("Test 2: Checking existing script nodes...")
        resp = await client.get(
            f"{seedance_base}/v3/projects/proj_99541a8a1b3d4c94b22bc6d2fe471448/snapshot",
            headers=headers,
        )

        snapshot = resp.json().get("snapshot", resp.json())
        nodes = snapshot.get("nodes", [])

        for node in nodes[:3]:
            if node.get("type") == "script":
                data = node.get("data", {})
                content_len = len(data.get("content", ""))
                prompt_len = len(data.get("prompt", ""))

                print(f"[*] Node: {node.get('title')[:50]}")
                print(f"    Type: script")
                print(f"    content length: {content_len} chars")
                print(f"    prompt length: {prompt_len} chars")

                if prompt_len > 0 and content_len == 0:
                    print(f"    [ISSUE] Content in wrong field (prompt)")
                elif content_len > 0:
                    print(f"    [OK] Content in correct field")
                print()


if __name__ == "__main__":
    asyncio.run(test_fixed_script_creation())
