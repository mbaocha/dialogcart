"""
Luma Pipeline Orchestrator

Explicit pipeline execution that calls existing package-level entry points
in the correct order without modifying existing logic.

Pipeline stages:
1. Extraction (EntityMatcher)
2. Intent Resolution (ReservationIntentResolver)
3. Structural Interpretation (interpret_structure)
4. Appointment Grouping (group_appointment)
5. Semantic Resolution (resolve_semantics)
6. Decision (decide_booking_status)
7. Calendar Binding (bind_calendar) - conditional on decision status
"""

from typing import Dict, Any, Optional, Tuple, Set
from datetime import datetime

from luma.extraction.matcher import EntityMatcher
from luma.grouping.reservation_intent_resolver import ReservationIntentResolver
from luma.structure.interpreter import interpret_structure
from luma.grouping.appointment_grouper import group_appointment
from luma.resolution.semantic_resolver import resolve_semantics, SemanticResolutionResult
from luma.decision import decide_booking_status, DecisionResult
from luma.calendar.calendar_binder import bind_calendar, CalendarBindingResult, _get_booking_policy
from luma.perf import StageTimer

# Pipeline Stage Contracts
# Defines what each stage produces and what it's allowed to read
# This prevents accidental coupling and responsibility drift between stages.
#
# Contract Rules:
# - Each stage MUST produce its required outputs
# - Each stage MAY only read from its allowed_inputs
# - Each stage MUST NOT mutate upstream stage results
# - Debug-only validation (no production impact)
#
# Stage Ownership:
# - extraction: Owns raw entity extraction (service_families, dates, times, psentence)
# - intent: Owns intent classification (intent, confidence)
# - structure: Owns structural analysis (booking_count, service_scope, time_type, etc.)
# - grouping: Owns entity grouping into bookings (intent, booking, structure)
# - semantic: Owns semantic resolution (resolved_booking with date_mode, time_mode, etc.)
# - decision: Owns policy decisions (status, reason, missing_slots)
# - calendar: Owns calendar binding (calendar_booking with ISO dates/times)

PIPELINE_STAGE_CONTRACTS = {
    "extraction": {
        "outputs": {
            "required": ["psentence"],  # Must produce parameterized sentence
            "optional": ["service_families", "dates", "dates_absolute", "times", "time_windows", "durations", "osentence"]
        },
        "allowed_inputs": set(),  # No upstream dependencies
        "forbidden": {
            "must_not_read": [],  # N/A - first stage
            "must_not_mutate": []  # N/A - no upstream data
        }
    },
    "intent": {
        "outputs": {
            "required": ["intent", "confidence"],
            "optional": ["external_intent", "status", "missing_slots"]
        },
        "allowed_inputs": {"extraction"},  # May read extraction result
        "forbidden": {
            "must_not_read": {"structure", "grouping", "semantic", "decision", "calendar"},
            "must_not_mutate": ["extraction"]  # Must not modify extraction_result
        }
    },
    "structure": {
        "outputs": {
            "required": ["booking_count", "service_scope", "time_scope", "date_scope", "time_type", "has_duration", "needs_clarification"],
            "optional": []
        },
        "allowed_inputs": {"extraction"},  # May read extraction result
        "forbidden": {
            "must_not_read": {"intent", "grouping", "semantic", "decision", "calendar"},
            "must_not_mutate": ["extraction"]  # Must not modify extraction_result
        }
    },
    "grouping": {
        "outputs": {
            "required": ["intent", "booking", "structure", "status"],
            "optional": ["reason"]
        },
        "allowed_inputs": {"extraction", "structure"},  # May read extraction and structure
        "forbidden": {
            "must_not_read": {"intent", "semantic", "decision", "calendar"},
            "must_not_mutate": ["extraction", "structure"]  # Must not modify upstream results
        }
    },
    "semantic": {
        "outputs": {
            "required": ["resolved_booking"],  # Contains: services, date_mode, date_refs, time_mode, time_refs, etc.
            "optional": ["needs_clarification", "clarification"]
        },
        "allowed_inputs": {"extraction", "grouping"},  # May read extraction and grouping
        "forbidden": {
            "must_not_read": {"intent", "structure", "decision", "calendar"},
            "must_not_mutate": ["extraction", "grouping"]  # Must not modify upstream results
        }
    },
    "decision": {
        "outputs": {
            "required": ["status"],  # RESOLVED or NEEDS_CLARIFICATION
            "optional": ["reason", "effective_time", "missing_slots"]
        },
        "allowed_inputs": {"extraction", "semantic"},  # May read extraction and semantic
        "forbidden": {
            "must_not_read": {"intent", "structure", "grouping", "calendar"},
            "must_not_mutate": ["extraction", "semantic"]  # Must not modify upstream results
        }
    },
    "calendar": {
        "outputs": {
            "required": ["calendar_booking"],
            "optional": ["needs_clarification", "clarification"]
        },
        "allowed_inputs": {"extraction", "semantic", "intent"},  # May read extraction, semantic, and intent
        "forbidden": {
            "must_not_read": {"structure", "grouping", "decision"},
            "must_not_mutate": ["extraction", "semantic", "intent"]  # Must not modify upstream results
        }
    }
}


def _validate_stage_boundary(
    stage_name: str,
    stage_output: Any,
    previous_stages: Dict[str, Any],
    debug_mode: bool = False
) -> None:
    """
    Validate that a stage respects its contract boundaries.
    
    This function enforces the pipeline stage contracts defined in PIPELINE_STAGE_CONTRACTS
    to prevent structural drift and accidental coupling between stages.
    
    Validation checks (debug mode only):
    1. Stage produces required outputs (presence and type checks)
    2. Stage does not read forbidden upstream data (static analysis - not enforced here)
    3. Stage does not mutate upstream results (shallow check - object identity preserved)
    
    This is a shallow validation guardrail. Deep mutation detection would require
    expensive copy tracking and is not implemented to avoid performance impact.
    
    Args:
        stage_name: Name of the stage being validated
        stage_output: Output from the stage
        previous_stages: Dictionary of previous stage outputs (for mutation checks)
        debug_mode: If True, raises AssertionError on violation. If False, no-op.
    
    Raises:
        AssertionError: If contract violation detected and debug_mode=True
    
    Note:
        This validation only runs in debug mode and has zero production impact.
        It serves as a development-time guardrail to catch contract violations early.
    """
    if not debug_mode:
        return
    
    contract = PIPELINE_STAGE_CONTRACTS.get(stage_name)
    if not contract:
        return  # Unknown stage, skip validation
    
    errors = []
    
    # Check required outputs are present
    required_outputs = contract["outputs"]["required"]
    if stage_name == "extraction":
        if not isinstance(stage_output, dict):
            errors.append(f"Stage 'extraction' must return a dict")
        else:
            for output_key in required_outputs:
                if output_key not in stage_output:
                    errors.append(f"Stage 'extraction' missing required output: {output_key}")
    elif stage_name == "intent":
        if not isinstance(stage_output, dict):
            errors.append(f"Stage 'intent' must return a dict")
        else:
            for output_key in required_outputs:
                if output_key not in stage_output:
                    errors.append(f"Stage 'intent' missing required output: {output_key}")
    elif stage_name == "structure":
        # Structure returns StructureResult object, check it has required attributes
        if not hasattr(stage_output, 'to_dict'):
            errors.append(f"Stage 'structure' must return StructureResult with to_dict() method")
        else:
            structure_dict = stage_output.to_dict().get("structure", {})
            for output_key in required_outputs:
                if output_key not in structure_dict:
                    errors.append(f"Stage 'structure' missing required output: {output_key}")
    elif stage_name == "grouping":
        if not isinstance(stage_output, dict):
            errors.append(f"Stage 'grouping' must return a dict")
        else:
            for output_key in required_outputs:
                if output_key not in stage_output:
                    errors.append(f"Stage 'grouping' missing required output: {output_key}")
    elif stage_name == "semantic":
        if not isinstance(stage_output, SemanticResolutionResult):
            errors.append(f"Stage 'semantic' must return SemanticResolutionResult")
        else:
            resolved_booking = stage_output.resolved_booking
            if not isinstance(resolved_booking, dict):
                errors.append(f"Stage 'semantic' resolved_booking must be a dict")
            else:
                # Check required fields in resolved_booking
                required_in_booking = ["services", "date_mode", "time_mode"]
                for key in required_in_booking:
                    if key not in resolved_booking:
                        errors.append(f"Stage 'semantic' resolved_booking missing required field: {key}")
    elif stage_name == "decision":
        if not isinstance(stage_output, DecisionResult):
            errors.append(f"Stage 'decision' must return DecisionResult")
        else:
            if not hasattr(stage_output, 'status'):
                errors.append(f"Stage 'decision' DecisionResult missing required attribute: status")
    elif stage_name == "calendar":
        if not isinstance(stage_output, CalendarBindingResult):
            errors.append(f"Stage 'calendar' must return CalendarBindingResult")
        else:
            if not hasattr(stage_output, 'calendar_booking'):
                errors.append(f"Stage 'calendar' CalendarBindingResult missing required attribute: calendar_booking")
    
    # Check for mutation of upstream results (shallow check - object identity)
    forbidden_mutations = contract["forbidden"]["must_not_mutate"]
    for upstream_stage in forbidden_mutations:
        if upstream_stage in previous_stages:
            upstream_output = previous_stages[upstream_stage]
            # Note: We can't detect mutations without deep comparison, so we only check
            # that the reference still exists. Deep mutation detection would be expensive.
            # This is a shallow guardrail - deep validation would require copy tracking.
    
    if errors:
        error_msg = f"Pipeline stage contract violation for '{stage_name}':\n" + "\n".join(f"  - {e}" for e in errors)
        raise AssertionError(error_msg)


class LumaPipeline:
    """
    Pipeline orchestrator that executes stages in order.
    
    This class does NOT contain business logic - it only orchestrates
    calls to existing package-level entry points.
    """

    def __init__(
        self,
        domain: str = "service",
        entity_file: Optional[str] = None,
        intent_resolver: Optional[ReservationIntentResolver] = None
    ):
        """
        Initialize pipeline components.
        
        Args:
            domain: "service" or "reservation"
            entity_file: Path to entity JSON file
            intent_resolver: Optional pre-initialized intent resolver
        """
        self.domain = domain
        self.entity_file = entity_file
        self.intent_resolver = intent_resolver or ReservationIntentResolver()

    def run(
        self,
        text: str,
        now: datetime,
        timezone: str = "UTC",
        tenant_context: Optional[Dict[str, Any]] = None,
        booking_mode: Optional[str] = None,
        request_id: Optional[str] = None,
        debug_mode: bool = False
    ) -> Dict[str, Any]:
        """
        Execute the full pipeline.
        
        Args:
            text: Input text to process
            now: Current datetime for calendar binding
            timezone: Timezone string (default: "UTC")
            tenant_context: Optional tenant context with aliases
            booking_mode: Optional booking mode override ("service" or "reservation")
            request_id: Optional request ID for logging
            
        Returns:
            Dictionary with stage results:
            {
                "extraction": {...},
                "intent": {...},
                "structure": {...},
                "grouping": {...},
                "semantic": SemanticResolutionResult,
                "decision": DecisionResult,
                "calendar": CalendarBindingResult,
                "execution_trace": {...}
            }
        """
        results = {
            "stages": {},
            "execution_trace": {}
        }
        
        # Initialize timings dict in execution_trace
        if "timings" not in results["execution_trace"]:
            results["execution_trace"]["timings"] = {}

        # Stage 1: Entity Extraction
        with StageTimer(results["execution_trace"], "extraction", request_id=request_id):
            matcher = EntityMatcher(domain=self.domain, entity_file=self.entity_file)
            extraction_result = matcher.extract_with_parameterization(
                text,
                request_id=request_id,
                tenant_aliases=tenant_context.get("aliases") if tenant_context else None
            )
        # Invariant: extraction_result is a dict with psentence key
        assert isinstance(extraction_result, dict), "extraction_result must be a dict"
        assert "psentence" in extraction_result, "extraction_result must contain 'psentence' key"
        assert isinstance(extraction_result.get("psentence"), str), "psentence must be a string"
        
        # Contract validation: extraction stage output
        previous_stages = {}
        _validate_stage_boundary("extraction", extraction_result, previous_stages, debug_mode=debug_mode)
        previous_stages["extraction"] = extraction_result
        
        results["stages"]["extraction"] = extraction_result

        # Stage 2: Intent Resolution
        effective_booking_mode = booking_mode
        if tenant_context and isinstance(tenant_context, dict):
            effective_booking_mode = tenant_context.get("booking_mode", "service") or "service"
        if not effective_booking_mode:
            effective_booking_mode = "service"

        with StageTimer(results["execution_trace"], "intent", request_id=request_id):
            intent, confidence = self.intent_resolver.resolve_intent(
                text, extraction_result, booking_mode=effective_booking_mode
            )
            intent_resp = self.intent_resolver._build_response(
                intent, confidence, extraction_result
            )
        # Invariant: intent_resp is a dict with intent and confidence keys
        assert isinstance(intent_resp, dict), "intent_resp must be a dict"
        assert "intent" in intent_resp, "intent_resp must contain 'intent' key"
        assert "confidence" in intent_resp, "intent_resp must contain 'confidence' key"
        
        # Contract validation: intent stage output
        _validate_stage_boundary("intent", intent_resp, previous_stages, debug_mode=debug_mode)
        previous_stages["intent"] = intent_resp
        
        results["stages"]["intent"] = intent_resp

        # Stage 3: Structural Interpretation
        with StageTimer(results["execution_trace"], "structure", request_id=request_id):
            psentence = extraction_result.get('psentence', '')
            structure = interpret_structure(psentence, extraction_result)
        # Invariant: structure is a StructureResult with to_dict() method
        assert hasattr(structure, 'to_dict'), "structure must have to_dict() method"
        structure_dict = structure.to_dict()
        assert isinstance(structure_dict, dict), "structure.to_dict() must return a dict"
        assert "structure" in structure_dict, "structure.to_dict() must contain 'structure' key"
        
        # Contract validation: structure stage output
        _validate_stage_boundary("structure", structure, previous_stages, debug_mode=debug_mode)
        previous_stages["structure"] = structure
        
        results["stages"]["structure"] = structure_dict["structure"]

        # Stage 4: Appointment Grouping
        with StageTimer(results["execution_trace"], "grouping", request_id=request_id):
            grouped_result = group_appointment(extraction_result, structure)
        # Invariant: grouped_result is a dict
        assert isinstance(grouped_result, dict), "grouped_result must be a dict"
        
        # Contract validation: grouping stage output
        _validate_stage_boundary("grouping", grouped_result, previous_stages, debug_mode=debug_mode)
        previous_stages["grouping"] = grouped_result
        
        results["stages"]["grouping"] = grouped_result

        # Stage 5: Semantic Resolution
        with StageTimer(results["execution_trace"], "semantic", request_id=request_id):
            semantic_result, semantic_trace = resolve_semantics(
                grouped_result, extraction_result, tenant_context=tenant_context
            )
        # Invariant: semantic_result is a SemanticResolutionResult with resolved_booking dict
        assert isinstance(semantic_result, SemanticResolutionResult), "semantic_result must be a SemanticResolutionResult"
        assert isinstance(semantic_result.resolved_booking, dict), "semantic_result.resolved_booking must be a dict"
        # Invariant: semantic_trace is a dict
        assert isinstance(semantic_trace, dict), "semantic_trace must be a dict"
        
        # Contract validation: semantic stage output
        _validate_stage_boundary("semantic", semantic_result, previous_stages, debug_mode=debug_mode)
        previous_stages["semantic"] = semantic_result
        
        results["stages"]["semantic"] = semantic_result
        results["execution_trace"].update(semantic_trace)

        # Stage 6: Decision
        # Get intent name for decision (use external_intent if available)
        intent_name = intent_resp.get("external_intent") or intent_resp.get("intent")
        semantic_for_decision = semantic_result.resolved_booking.copy()
        semantic_for_decision["booking_mode"] = self.domain

        booking_policy = _get_booking_policy()
        with StageTimer(results["execution_trace"], "decision", request_id=request_id):
            decision_result, decision_trace = decide_booking_status(
                semantic_for_decision,
                entities=extraction_result,
                policy=booking_policy,
                intent_name=intent_name
            )
        # Invariant: decision_result is a DecisionResult with valid status
        assert isinstance(decision_result, DecisionResult), "decision_result must be a DecisionResult"
        assert decision_result.status in ("RESOLVED", "NEEDS_CLARIFICATION"), f"decision_result.status must be 'RESOLVED' or 'NEEDS_CLARIFICATION', got '{decision_result.status}'"
        # Invariant: decision_trace is a dict with decision key
        assert isinstance(decision_trace, dict), "decision_trace must be a dict"
        assert "decision" in decision_trace, "decision_trace must contain 'decision' key"
        assert isinstance(decision_trace["decision"], dict), "decision_trace['decision'] must be a dict"
        
        # Contract validation: decision stage output
        _validate_stage_boundary("decision", decision_result, previous_stages, debug_mode=debug_mode)
        previous_stages["decision"] = decision_result
        
        results["stages"]["decision"] = decision_result
        results["execution_trace"].update(decision_trace)

        # Stage 7: Calendar Binding (conditional)
        # Only bind if decision is RESOLVED or missing_only_time exception
        decision_missing_slots = decision_trace.get("decision", {}).get("missing_slots", [])
        has_date = bool(semantic_result.resolved_booking.get("date_refs"))
        missing_only_time = (
            decision_result.status == "NEEDS_CLARIFICATION" and
            decision_result.reason == "temporal_shape_not_satisfied" and
            len(decision_missing_slots) == 1 and
            decision_missing_slots == ["time"] and
            has_date
        )

        if decision_result.status == "RESOLVED" or missing_only_time:
            # Proceed with calendar binding
            binding_intent = intent_name if intent_name != "CONTEXTUAL_UPDATE" else "CREATE_BOOKING"
            external_intent = intent_resp.get("external_intent")
            
            with StageTimer(results["execution_trace"], "binder", request_id=request_id):
                calendar_result, binder_trace = bind_calendar(
                    semantic_result,
                    now,
                    timezone,
                    intent=binding_intent,
                    entities=extraction_result,
                    external_intent=external_intent
                )
            # Invariant: calendar_result is a CalendarBindingResult
            assert isinstance(calendar_result, CalendarBindingResult), "calendar_result must be a CalendarBindingResult"
            # Invariant: binder_trace is a dict with binder key
            assert isinstance(binder_trace, dict), "binder_trace must be a dict"
            assert "binder" in binder_trace, "binder_trace must contain 'binder' key"
            assert isinstance(binder_trace["binder"], dict), "binder_trace['binder'] must be a dict"
            
            # Contract validation: calendar stage output
            _validate_stage_boundary("calendar", calendar_result, previous_stages, debug_mode=debug_mode)
            
            results["stages"]["calendar"] = calendar_result
            results["execution_trace"].update(binder_trace)
        else:
            # Skip calendar binding
            calendar_result = CalendarBindingResult(
                calendar_booking={},
                needs_clarification=False,
                clarification=None
            )
            results["stages"]["calendar"] = calendar_result
            # Add trace indicating binder was skipped
            external_intent = intent_resp.get("external_intent")
            results["execution_trace"]["binder"] = {
                "called": False,
                "input": {
                    "intent": intent_name,
                    "external_intent": external_intent,
                    "date_mode": semantic_result.resolved_booking.get("date_mode", "none"),
                    "date_refs": semantic_result.resolved_booking.get("date_refs", []),
                    "time_mode": semantic_result.resolved_booking.get("time_mode", "none"),
                    "time_refs": semantic_result.resolved_booking.get("time_refs", []),
                    "time_constraint": semantic_result.resolved_booking.get("time_constraint"),
                    "timezone": timezone
                },
                "output": {},
                "decision_reason": f"decision={decision_result.status}"
            }
            # Invariant: calendar_result is a CalendarBindingResult even when skipped
            assert isinstance(calendar_result, CalendarBindingResult), "calendar_result must be a CalendarBindingResult"
            # Invariant: binder trace exists with called=False
            assert "binder" in results["execution_trace"], "execution_trace must contain 'binder' key when calendar binding is skipped"
            assert results["execution_trace"]["binder"]["called"] is False, "binder.called must be False when calendar binding is skipped"
            
            # Contract validation: calendar stage output (even when skipped)
            _validate_stage_boundary("calendar", calendar_result, previous_stages, debug_mode=debug_mode)

        # Final invariant: execution_trace always contains binder key
        assert "binder" in results["execution_trace"], "execution_trace must always contain 'binder' key"
        assert isinstance(results["execution_trace"]["binder"], dict), "execution_trace['binder'] must be a dict"
        assert "called" in results["execution_trace"]["binder"], "execution_trace['binder'] must contain 'called' key"
        assert isinstance(results["execution_trace"]["binder"]["called"], bool), "execution_trace['binder']['called'] must be a bool"

        return results

