"""Private process entrypoint for a long-lived Pactree extension runtime."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import threading
from pathlib import Path
from typing import Sequence

from pactree.context import ContextTree

from .runtime_server import ExtensionRuntimeServer
from .services import StateRoots


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--pact", required=True, type=Path)
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--extension", action="append", default=[])
    parser.add_argument("--create-context-tree-id")
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument("--ready-fd", required=True, type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    ready = socket.socket(fileno=args.ready_fd)
    server: ExtensionRuntimeServer | None = None
    stopped = threading.Event()

    def request_stop(_signal: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        if ready.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_SEQPACKET:
            raise RuntimeError("runtime readiness channel must be SOCK_SEQPACKET")
        if args.parent_pid <= 1 or os.getppid() != args.parent_pid:
            raise RuntimeError("runtime parent identity changed before startup")

        pact_path = args.pact.expanduser().resolve()
        if pact_path.exists():
            tree = ContextTree.from_file(pact_path)
        else:
            context_tree_id = str(args.create_context_tree_id or "")
            if not context_tree_id:
                raise RuntimeError("missing PACT requires a context-tree identity")
            pact_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            tree = ContextTree.with_id(context_tree_id)
            tree.save(pact_path)

        state_root = args.state_root.expanduser().resolve()
        state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(state_root, 0o700)
        state_roots = StateRoots(
            data=state_root / "data",
            cache=state_root / "cache",
            runtime=state_root / "runtime",
        )
        for root in (state_roots.data, state_roots.cache, state_roots.runtime):
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(root, 0o700)
        server = ExtensionRuntimeServer(
            tree,
            extensions=tuple(args.extension),
            runtime_root=args.runtime_root,
            state_roots=state_roots,
        ).start()
        payload = {
            "ok": True,
            "pid": os.getpid(),
            **server.connection_descriptor(),
        }
        ready.send(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        ready.close()
        while not stopped.wait(0.2):
            if os.getppid() != args.parent_pid:
                break
        return 0
    except BaseException:
        try:
            ready.send(
                json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": "START_FAILED",
                            "message": "extension runtime failed to start",
                        },
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            )
        except OSError:
            pass
        return 125
    finally:
        ready.close()
        if server is not None:
            server.close()


if __name__ == "__main__":
    raise SystemExit(main())
