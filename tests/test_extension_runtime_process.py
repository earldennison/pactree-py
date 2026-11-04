from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path

from pactree.extensions.runtime_protocol import ExtensionRuntimeClient


def test_runtime_process_bootstraps_over_inherited_fd_without_leaking_capability(
    tmp_path: Path,
) -> None:
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    parent.settimeout(15)
    pact_path = tmp_path / "session.pact"
    runtime_root = tmp_path / "endpoint"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pactree.extensions.runtime_process",
            "--pact",
            str(pact_path),
            "--state-root",
            str(tmp_path / "state"),
            "--runtime-root",
            str(runtime_root),
            "--create-context-tree-id",
            "runtime-process-owner",
            "--parent-pid",
            str(os.getpid()),
            "--ready-fd",
            str(child.fileno()),
        ],
        pass_fds=(child.fileno(),),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child.close()
    capability = ""
    try:
        descriptor = json.loads(parent.recv(64 * 1024))
        assert descriptor["ok"] is True
        assert descriptor["pid"] == process.pid
        capability = str(descriptor["capability"])
        assert capability
        command_line = Path(f"/proc/{process.pid}/cmdline").read_bytes()
        environment = Path(f"/proc/{process.pid}/environ").read_bytes()
        assert capability.encode() not in command_line
        assert capability.encode() not in environment

        client = ExtensionRuntimeClient(
            socket_path=descriptor["socket_path"],
            capability=capability,
            server_generation=descriptor["server_generation"],
        )
        probe = client.probe()
        assert probe["server_generation"] == descriptor["server_generation"]
        assert probe["extension_generation"] == descriptor["extension_generation"]
        assert pact_path.read_bytes().startswith(b"PACTBIN2")
        assert stat.S_IMODE((tmp_path / "state").stat().st_mode) == 0o700
    finally:
        parent.close()
        process.terminate()
        stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 0
    assert capability not in stdout
    assert capability not in stderr
    assert not (runtime_root / "runtime.sock").exists()
