#!/usr/bin/env python3
"""
Sandbox setup script — pre-pulls the Docker image so first execution is instant.
Run this once after deploying OmniAgent.

Usage:
    uv run python scripts/setup_sandbox.py
    # or
    python scripts/setup_sandbox.py
"""

import asyncio
import subprocess
import sys


SANDBOX_IMAGE = "python:3.12-slim"


def check_docker() -> bool:
    """Check if Docker is running."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


async def pull_image() -> bool:
    """Pull the sandbox Docker image."""
    print(f"📦 Pulling sandbox image: {SANDBOX_IMAGE}...")
    proc = await asyncio.create_subprocess_exec(
        "docker", "pull", SANDBOX_IMAGE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    if proc.returncode == 0:
        print(f"✅ Image '{SANDBOX_IMAGE}' ready!")
        return True
    else:
        print(f"❌ Failed to pull image: {stderr.decode()}")
        return False


async def test_sandbox() -> bool:
    """Run a quick smoke test of the sandbox."""
    print("🔬 Testing sandbox execution...")
    proc = await asyncio.create_subprocess_exec(
        "docker", "run", "--rm",
        "--memory=64m",
        "--security-opt", "no-new-privileges:true",
        SANDBOX_IMAGE,
        "python3", "-c",
        "import sys; print(f'Python {sys.version_info.major}.{sys.version_info.minor} ✅')",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode == 0:
        print(f"✅ Sandbox test passed: {stdout.decode().strip()}")
        return True
    else:
        print(f"❌ Sandbox test failed: {stderr.decode()}")
        return False


async def main():
    print("=" * 50)
    print("  OmniAgent Sandbox Setup")
    print("=" * 50)

    if not check_docker():
        print("❌ Docker is not running! Start Docker first:")
        print("   sudo systemctl start docker")
        sys.exit(1)
    print("✅ Docker is running")

    ok = await pull_image()
    if not ok:
        sys.exit(1)

    ok = await test_sandbox()
    if not ok:
        sys.exit(1)

    print()
    print("✅ Sandbox setup complete!")
    print("   The AI can now use run_sandbox_command() to execute code.")


if __name__ == "__main__":
    asyncio.run(main())
