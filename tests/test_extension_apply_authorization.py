from __future__ import annotations

import time
from pathlib import Path

import pytest

import pactree
from pactree.extensions import (
    ApplyAuthorization,
    ExtensionAuthorizationError,
    bind_context_tree,
)


def test_apply_authorization_is_scope_snapshot_target_bound_and_single_use(
    tmp_path: Path,
) -> None:
    path = tmp_path / "authorization.pact"
    tree = pactree.ContextTree.with_id("authorization-owner")
    tree.save(path)

    with bind_context_tree(tree) as host:
        lane_a = host.view_for("lane-a")
        lane_b = host.view_for("lane-b")
        authorization = host.authorize(
            lane_a,
            capability="pactree.fs.apply",
            expected_operation={"candidate": "candidate-a"},
            expected_snapshot=lane_a.snapshot,
            expected_target_head="checkpoint-a",
            actor="agent:a",
        )

        with pytest.raises(ExtensionAuthorizationError, match="scope"):
            host.consume_authorization(
                lane_b,
                authorization,
                capability="pactree.fs.apply",
                expected_operation={"candidate": "candidate-a"},
                expected_snapshot=lane_a.snapshot,
                expected_target_head="checkpoint-a",
            )

        attestation = host.consume_authorization(
            lane_a,
            authorization,
            capability="pactree.fs.apply",
            expected_operation={"candidate": "candidate-a"},
            expected_snapshot=lane_a.snapshot,
            expected_target_head="checkpoint-a",
        )
        assert attestation.actor == "agent:a"
        assert attestation.capability == "pactree.fs.apply"

        with pytest.raises(ExtensionAuthorizationError, match="consumed"):
            host.consume_authorization(
                lane_a,
                authorization,
                capability="pactree.fs.apply",
                expected_operation={"candidate": "candidate-a"},
                expected_snapshot=lane_a.snapshot,
                expected_target_head="checkpoint-a",
            )


def test_apply_authorization_rejects_forgery_expiry_and_target_mismatch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "authorization-errors.pact"
    tree = pactree.ContextTree.with_id("authorization-errors-owner")
    tree.save(path)

    with pytest.raises(TypeError):
        ApplyAuthorization("forged")  # type: ignore[call-arg]

    with bind_context_tree(tree) as host:
        view = host.view_for("lane-a")
        target_bound = host.authorize(
            view,
            capability="pactree.fs.apply",
            expected_operation={"candidate": "candidate-a"},
            expected_snapshot=view.snapshot,
            expected_target_head="checkpoint-a",
            actor="agent:a",
        )
        with pytest.raises(ExtensionAuthorizationError, match="operation"):
            host.consume_authorization(
                view,
                target_bound,
                capability="pactree.fs.apply",
                expected_operation={"candidate": "candidate-b"},
                expected_snapshot=view.snapshot,
                expected_target_head="checkpoint-a",
            )
        with pytest.raises(ExtensionAuthorizationError, match="target"):
            host.consume_authorization(
                view,
                target_bound,
                capability="pactree.fs.apply",
                expected_operation={"candidate": "candidate-a"},
                expected_snapshot=view.snapshot,
                expected_target_head="checkpoint-b",
            )

        expired = host.authorize(
            view,
            capability="pactree.fs.apply",
            expected_operation={"candidate": "candidate-a"},
            expected_snapshot=view.snapshot,
            expected_target_head="checkpoint-a",
            actor="agent:a",
            ttl_seconds=0.001,
        )
        time.sleep(0.01)
        with pytest.raises(ExtensionAuthorizationError, match="expired"):
            host.consume_authorization(
                view,
                expired,
                capability="pactree.fs.apply",
                expected_operation={"candidate": "candidate-a"},
                expected_snapshot=view.snapshot,
                expected_target_head="checkpoint-a",
            )
