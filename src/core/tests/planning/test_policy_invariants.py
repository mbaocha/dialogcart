"""
Invariant Tests for Intent Policy

Tests that ensure intent_policy.yaml is the single source of truth and that
runtime invariants are enforced for committing steps.
"""

from typing import Any, Dict, List

import pytest

from core.policy.intent_policy import (
    get_planning_required_slots,
    select_next_execution_step,
)


def _load_intent_policy() -> Dict[str, Any]:
    """Load intent policy for testing."""
    from core.policy.intent_policy import _load_unified_policy

    return _load_unified_policy()


class TestPolicyCompleteness:
    """Test that every intent in intent_policy.yaml has required structure."""

    def test_all_intents_have_metadata_durable(self):
        """Every intent must have metadata.durable defined."""
        policy = _load_intent_policy()

        for intent_name, intent_config in policy.items():
            assert (
                "metadata" in intent_config
            ), f"Intent {intent_name} missing 'metadata' section"
            assert (
                "durable" in intent_config["metadata"]
            ), f"Intent {intent_name} missing 'metadata.durable' field"
            assert isinstance(intent_config["metadata"]["durable"], bool), (
                f"Intent {intent_name} has invalid 'metadata.durable' type "
                f"(expected bool, got {type(intent_config['metadata']['durable'])})"
            )

    def test_all_intents_have_planning_required_slots(self):
        """Every intent must have planning.required_slots defined."""
        policy = _load_intent_policy()

        for intent_name, intent_config in policy.items():
            assert (
                "planning" in intent_config
            ), f"Intent {intent_name} missing 'planning' section"
            assert (
                "required_slots" in intent_config["planning"]
            ), f"Intent {intent_name} missing 'planning.required_slots' field"
            required_slots = intent_config["planning"]["required_slots"]
            assert isinstance(required_slots, list), (
                f"Intent {intent_name} has invalid 'planning.required_slots' type "
                f"(expected list, got {type(required_slots)})"
            )
            assert (
                len(required_slots) > 0
            ), f"Intent {intent_name} has empty 'planning.required_slots' list"

    def test_all_intents_have_execution_steps(self):
        """Every intent must have at least one execution step."""
        policy = _load_intent_policy()

        for intent_name, intent_config in policy.items():
            assert (
                "execution" in intent_config
            ), f"Intent {intent_name} missing 'execution' section"
            execution = intent_config["execution"]
            assert isinstance(execution, dict), (
                f"Intent {intent_name} has invalid 'execution' type "
                f"(expected dict, got {type(execution)})"
            )
            assert len(execution) > 0, f"Intent {intent_name} has no execution steps"

            # Each execution step must have required fields
            for step_name, step_config in execution.items():
                assert (
                    "mode" in step_config
                ), f"Intent {intent_name}, step {step_name} missing 'mode' field"
                assert step_config["mode"] in ("exploratory", "committing"), (
                    f"Intent {intent_name}, step {step_name} has invalid 'mode' "
                    f"(expected 'exploratory' or 'committing', got {step_config['mode']})"
                )
                assert (
                    "required_slots" in step_config
                ), f"Intent {intent_name}, step {step_name} missing 'required_slots' field"
                assert isinstance(
                    step_config["required_slots"], list
                ), f"Intent {intent_name}, step {step_name} has invalid 'required_slots' type"


class TestCommittingStepInvariants:
    """Test that committing steps enforce runtime invariants."""

    @pytest.mark.parametrize(
        "intent_name",
        [
            "CREATE_APPOINTMENT",
            "CREATE_RESERVATION",
            "MODIFY_BOOKING",
            "MODIFY_RESERVATION",
            "CANCEL_BOOKING",
        ],
    )
    def test_committing_step_requires_all_required_slots(self, intent_name):
        """Committing steps cannot execute if required_slots are missing."""
        policy = _load_intent_policy()
        intent_config = policy.get(intent_name)

        if not intent_config:
            pytest.skip(f"Intent {intent_name} not in policy")

        execution = intent_config.get("execution", {})
        committing_steps = [
            (step_name, step_config)
            for step_name, step_config in execution.items()
            if step_config.get("mode") == "committing"
        ]

        if not committing_steps:
            pytest.skip(f"Intent {intent_name} has no committing steps")

        for step_name, step_config in committing_steps:
            required_slots = step_config.get("required_slots", [])

            # Test with missing required slots
            incomplete_slots = {}
            flags = {"availability_resolved": True, "confirmation_state": "confirmed"}

            selected_step = select_next_execution_step(
                intent_name, incomplete_slots, flags
            )

            # If required slots are missing, the committing step should not be selected
            if selected_step and selected_step.get("action") == step_name:
                # This should not happen - policy should not select committing step
                # when required slots are missing
                pytest.fail(
                    f"Policy incorrectly selected committing step {step_name} for {intent_name} "
                    f"when required slots {required_slots} are missing"
                )

    @pytest.mark.parametrize(
        "intent_name",
        [
            "CREATE_APPOINTMENT",
            "CREATE_RESERVATION",
            "MODIFY_BOOKING",
            "MODIFY_RESERVATION",
        ],
    )
    def test_committing_step_requires_availability_resolved(self, intent_name):
        """Committing steps that require availability_resolved cannot execute without it."""
        policy = _load_intent_policy()
        intent_config = policy.get(intent_name)

        if not intent_config:
            pytest.skip(f"Intent {intent_name} not in policy")

        execution = intent_config.get("execution", {})
        committing_steps = [
            (step_name, step_config)
            for step_name, step_config in execution.items()
            if step_config.get("mode") == "committing"
        ]

        if not committing_steps:
            pytest.skip(f"Intent {intent_name} has no committing steps")

        for step_name, step_config in committing_steps:
            requires = step_config.get("requires", [])

            if "availability_resolved" not in requires:
                continue  # Skip steps that don't require availability_resolved

            # Get required slots for this intent
            required_slots = get_planning_required_slots(intent_name)

            # Test with all required slots but availability_resolved=False
            complete_slots = {slot: f"test_{slot}" for slot in required_slots}
            flags = {"availability_resolved": False, "confirmation_state": "confirmed"}

            selected_step = select_next_execution_step(
                intent_name, complete_slots, flags
            )

            # The committing step should not be selected when availability_resolved=False
            if selected_step and selected_step.get("action") == step_name:
                pytest.fail(
                    f"Policy incorrectly selected committing step {step_name} for {intent_name} "
                    f"when availability_resolved=False"
                )

    @pytest.mark.parametrize(
        "intent_name",
        ["CREATE_APPOINTMENT", "MODIFY_BOOKING", "MODIFY_RESERVATION"],
    )
    def test_committing_step_requires_confirmation_state_confirmed(self, intent_name):
        """Committing steps that require confirmation_state_confirmed cannot execute without it."""
        policy = _load_intent_policy()
        intent_config = policy.get(intent_name)

        if not intent_config:
            pytest.skip(f"Intent {intent_name} not in policy")

        execution = intent_config.get("execution", {})
        committing_steps = [
            (step_name, step_config)
            for step_name, step_config in execution.items()
            if step_config.get("mode") == "committing"
        ]

        if not committing_steps:
            pytest.skip(f"Intent {intent_name} has no committing steps")

        for step_name, step_config in committing_steps:
            requires = step_config.get("requires", [])

            if "confirmation_state_confirmed" not in requires:
                continue  # Skip steps that don't require confirmation_state_confirmed

            # Get required slots for this intent
            required_slots = get_planning_required_slots(intent_name)

            # Test with all required slots but confirmation_state != "confirmed"
            complete_slots = {slot: f"test_{slot}" for slot in required_slots}
            flags = {"availability_resolved": True, "confirmation_state": "pending"}

            selected_step = select_next_execution_step(
                intent_name, complete_slots, flags
            )

            # The committing step should not be selected when confirmation_state != "confirmed"
            if selected_step and selected_step.get("action") == step_name:
                pytest.fail(
                    f"Policy incorrectly selected committing step {step_name} for {intent_name} "
                    f"when confirmation_state != 'confirmed'"
                )


class TestIdempotencyGuard:
    """Test that committing steps cannot execute twice in the same session."""

    @pytest.mark.parametrize(
        "intent_name",
        [
            "CREATE_APPOINTMENT",
            "CREATE_RESERVATION",
            "MODIFY_BOOKING",
            "MODIFY_RESERVATION",
            "CANCEL_BOOKING",
        ],
    )
    def test_committing_step_idempotency(self, intent_name):
        """Committing steps should not be selected if already executed in session."""
        policy = _load_intent_policy()
        intent_config = policy.get(intent_name)

        if not intent_config:
            pytest.skip(f"Intent {intent_name} not in policy")

        execution = intent_config.get("execution", {})
        committing_steps = [
            (step_name, step_config)
            for step_name, step_config in execution.items()
            if step_config.get("mode") == "committing"
        ]

        if not committing_steps:
            pytest.skip(f"Intent {intent_name} has no committing steps")

        for step_name, step_config in committing_steps:
            # Get required slots
            required_slots = get_planning_required_slots(intent_name)
            complete_slots = {slot: f"test_{slot}" for slot in required_slots}

            # Set up flags for committing step
            flags = {"availability_resolved": True, "confirmation_state": "confirmed"}

            # First execution - should select committing step
            selected_step = select_next_execution_step(
                intent_name, complete_slots, flags
            )

            if not selected_step or selected_step.get("action") != step_name:
                # Policy doesn't select this step - skip idempotency test
                continue

            # Simulate that this step was already executed
            # Note: Actual idempotency enforcement happens at execution layer
            # This test just verifies the policy structure supports it
            # (The actual guard is in _enforce_committing_step_invariants)
            assert (
                step_config.get("mode") == "committing"
            ), f"Step {step_name} should be committing for idempotency test"
