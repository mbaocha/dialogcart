#!/usr/bin/env python3
"""
Interactive CLI for Luma Service/Reservation Booking Pipeline

A REPL (Read-Eval-Print Loop) for testing the service booking pipeline interactively.
Useful for manual testing, debugging, and demonstration.

Features:
- Warm startup (preloads models)
- Pretty-printed results
- Real-time pipeline testing
- Full pipeline stages: extraction → intent → structure → grouping → semantic → calendar

Usage:
    python -m luma.cli.interactive
    
    or
    
    cd src
    python luma/cli/interactive.py
"""
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

# Add src/ to path if running directly
if __name__ == "__main__":
    src_path = Path(__file__).parent.parent.parent
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

from luma.calendar.calendar_binder import bind_calendar
from luma.resolution.semantic_resolver import resolve_semantics
from luma.grouping.appointment_grouper import group_appointment
from luma.structure.interpreter import interpret_structure
from luma.grouping.reservation_intent_resolver import ReservationIntentResolver
from luma.extraction.matcher import EntityMatcher
from luma.clarification import render_clarification


def find_normalization_dir():
    """Find the normalization directory."""
    current_file = Path(__file__).resolve()
    # From luma/cli/interactive.py -> luma/store/normalization
    # parent = luma/cli/, parent.parent = luma/, so luma/store/normalization/
    store_dir = current_file.parent.parent / "store" / "normalization"
    if store_dir.exists():
        return store_dir.resolve()
    # Fallback: try src/intents/normalization
    src_dir = current_file.parent.parent.parent
    intents_norm = src_dir / "intents" / "normalization"
    if intents_norm.exists():
        return intents_norm.resolve()
    return None


def _localize_datetime(dt: datetime, timezone: str) -> datetime:
    """Localize datetime to timezone."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(timezone)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=tz)
        return dt.astimezone(tz)
    except Exception:
        try:
            import pytz
            tz = pytz.timezone(timezone)
            if dt.tzinfo is None:
                return tz.localize(dt)
            return dt.astimezone(tz)
        except Exception:
            return dt


def print_banner():
    """Print welcome banner."""
    print("\n" + "=" * 60)
    print("📅 Luma Service/Reservation Booking - Interactive Mode")
    print("=" * 60)
    print("\nCommands:")
    print("  - Type a booking request to process")
    print("  - Type 'quit' or 'exit' to quit")
    print("  - Press Ctrl+C to exit")
    print("\nExamples:")
    print("  - 'book haircut tomorrow at 2pm'")
    print("  - 'I want a massage this Friday morning'")
    print("  - 'schedule facial treatment next Monday'")
    print("=" * 60)


def print_final_result(result: Dict[str, Any]):
    """
    Print only the serialized calendar result (final response object).

    Args:
        result: Dictionary with results from each pipeline stage
    """
    import json

    stages = result.get("stages", {})

    # Check for errors
    extraction = stages.get("extraction", {})
    if "error" in extraction:
        print(f"\n❌ Error: {extraction['error']}")
        return

    # Get final calendar binding result (serialized)
    calendar = stages.get("calendar", {})
    if "error" in calendar:
        print(f"\n❌ Error: {calendar['error']}")
        return

    # Print the serialized calendar result as JSON
    print()  # Empty line for spacing
    print(json.dumps(calendar, indent=2, ensure_ascii=False))


def print_pipeline_result(result: Dict[str, Any], verbose: bool = False):
    """
    Print pipeline result (detailed or simplified based on verbose flag).

    Args:
        result: Dictionary with results from each pipeline stage
        verbose: If True, show detailed stage-by-stage output; if False, show only final result
    """
    if not verbose:
        print_final_result(result)
        return

    # Verbose mode: show all stages
    stages = result.get("stages", {})

    # Stage 1: Extraction
    extraction = stages.get("extraction", {})
    if "error" in extraction:
        print(f"\n❌ Extraction Error: {extraction['error']}")
        return

    print(f"\n{'='*60}")
    print("📋 EXTRACTION RESULTS")
    print(f"{'='*60}")
    psentence = extraction.get("psentence")
    if psentence:
        print(f"Psentence: {psentence}")
    business_categories = extraction.get('business_categories') or extraction.get('service_families', [])
    print(f"Services: {len(business_categories)}")
    for svc in business_categories:
        print(f"  - {svc.get('text')} ({svc.get('canonical')})")
    print(
        f"Dates: {len(extraction.get('dates', [])) + len(extraction.get('dates_absolute', []))}")
    print(f"Times: {len(extraction.get('times', []))}")
    print(f"Time Windows: {len(extraction.get('time_windows', []))}")

    # Stage 2: Intent
    intent = stages.get("intent", {})
    if "error" not in intent:
        print(f"\n{'='*60}")
        print("🎯 INTENT RESOLUTION")
        print(f"{'='*60}")
        print(f"Intent: {intent.get('intent')}")
        print(f"Confidence: {intent.get('confidence')}")

    # Stage 3: Structure
    structure = stages.get("structure", {})
    if "error" not in structure:
        print(f"\n{'='*60}")
        print("🏗️  STRUCTURAL INTERPRETATION")
        print(f"{'='*60}")
        print(f"Booking Count: {structure.get('booking_count')}")
        print(f"Service Scope: {structure.get('service_scope')}")
        print(f"Time Scope: {structure.get('time_scope')}")
        print(f"Date Scope: {structure.get('date_scope')}")
        print(f"Time Type: {structure.get('time_type')}")

    # Stage 4: Grouping
    grouping = stages.get("grouping", {})
    if "error" not in grouping:
        print(f"\n{'='*60}")
        print("📦 APPOINTMENT GROUPING")
        print(f"{'='*60}")
        booking = grouping.get("booking", {})
        print(f"Intent: {grouping.get('intent')}")
        print(f"Status: {grouping.get('status')}")
        print(f"Services: {len(booking.get('services', []))}")
        print(f"Date Ref: {booking.get('date_ref')}")
        print(f"Time Ref: {booking.get('time_ref')}")

    # Stage 5: Semantic Resolution
    semantic = stages.get("semantic", {})
    if "error" not in semantic:
        print(f"\n{'='*60}")
        print("🧠 SEMANTIC RESOLUTION")
        print(f"{'='*60}")
        resolved = semantic.get("resolved_booking", {})
        print(f"Date Mode: {resolved.get('date_mode')}")
        print(f"Date Refs: {resolved.get('date_refs')}")
        print(f"Time Mode: {resolved.get('time_mode')}")
        print(f"Time Refs: {resolved.get('time_refs')}")
        print(f"Needs Clarification: {semantic.get('needs_clarification')}")

        clarification = semantic.get("clarification")
        if clarification:
            try:
                from luma.clarification import Clarification, ClarificationReason
                clar = Clarification(
                    reason=ClarificationReason(clarification["reason"]),
                    data=clarification.get("data", {})
                )
                message = render_clarification(clar)
                print(f"⚠️  Clarification: {message}")
            except Exception:
                print(f"⚠️  Clarification: {clarification.get('reason')}")

    # Stage 6: Calendar Binding
    calendar = stages.get("calendar", {})
    if "error" not in calendar:
        print(f"\n{'='*60}")
        print("📅 CALENDAR BINDING")
        print(f"{'='*60}")
        booking = calendar.get("calendar_booking", {})
        if booking:
            print(f"Date Range: {booking.get('date_range')}")
            print(f"Time Range: {booking.get('time_range')}")
            print(f"Datetime Range: {booking.get('datetime_range')}")
        else:
            print("No calendar binding (intent-guarded)")

        print(f"Needs Clarification: {calendar.get('needs_clarification')}")
        clarification = calendar.get("clarification")
        if clarification:
            try:
                from luma.clarification import Clarification, ClarificationReason
                clar = Clarification(
                    reason=ClarificationReason(clarification["reason"]),
                    data=clarification.get("data", {})
                )
                message = render_clarification(clar)
                print(f"⚠️  Clarification: {message}")
            except Exception:
                print(f"⚠️  Clarification: {clarification.get('reason')}")


def run_pipeline(sentence: str, domain: str = "service", timezone: str = "UTC") -> Dict[str, Any]:
    """
    Run the complete service/reservation booking pipeline.

    Args:
        sentence: User input sentence
        domain: "service" or "reservation"
        timezone: Timezone string (e.g., "UTC", "America/New_York")

    Returns:
        Dictionary with results from each pipeline stage
    """
    now = datetime.now()
    now = _localize_datetime(now, timezone)

    # Find normalization directory
    normalization_dir = find_normalization_dir()
    if not normalization_dir:
        return {"error": "Normalization directory not found"}
    entity_file = str(normalization_dir / "101.v2.json")

    results = {
        "input": {
            "sentence": sentence,
            "domain": domain,
            "timezone": timezone,
            "now": now.isoformat()
        },
        "stages": {}
    }

    # Stage 1: Entity Extraction
    try:
        matcher = EntityMatcher(domain=domain, entity_file=entity_file)
        extraction_result = matcher.extract_with_parameterization(sentence)
        results["stages"]["extraction"] = extraction_result
    except Exception as e:
        results["stages"]["extraction"] = {"error": str(e)}
        return results

    # Stage 2: Intent Resolution
    try:
        intent_resolver = ReservationIntentResolver()
        intent, confidence = intent_resolver.resolve_intent(
            sentence, extraction_result)
        results["stages"]["intent"] = {
            "intent": intent, "confidence": confidence}
    except Exception as e:
        results["stages"]["intent"] = {"error": str(e)}
        return results

    # Stage 3: Structural Interpretation
    try:
        psentence = extraction_result.get('psentence', '')
        structure = interpret_structure(psentence, extraction_result)
        results["stages"]["structure"] = structure.to_dict()["structure"]
    except Exception as e:
        results["stages"]["structure"] = {"error": str(e)}
        return results

    # Stage 4: Appointment Grouping
    try:
        grouped_result = group_appointment(extraction_result, structure)
        results["stages"]["grouping"] = grouped_result
    except Exception as e:
        results["stages"]["grouping"] = {"error": str(e)}
        return results

    # Stage 5: Semantic Resolution
    try:
        semantic_result = resolve_semantics(grouped_result, extraction_result)
        results["stages"]["semantic"] = semantic_result.to_dict()
    except Exception as e:
        results["stages"]["semantic"] = {"error": str(e)}
        return results

    # Stage 6: Calendar Binding
    try:
        calendar_result = bind_calendar(
            semantic_result,
            now,
            timezone,
            intent=intent,
            entities=extraction_result
        )
        results["stages"]["calendar"] = calendar_result.to_dict()
    except Exception as e:
        results["stages"]["calendar"] = {"error": str(e)}
        return results

    return results


def interactive_main(verbose: bool = False):
    """
    Interactive mode for testing service/reservation booking pipeline.

    Args:
        verbose: If True, show detailed stage-by-stage output; if False, show only final result
    """
    print_banner()
    if not verbose:
        print("\n💡 Tip: Use --verbose flag to see detailed stage-by-stage output")

    # Main REPL loop
    while True:
        try:
            # Get user input
            sentence = input("\n💬 Enter booking request: ").strip()

            # Check for exit commands
            if not sentence or sentence.lower() in ['quit', 'exit', 'q']:
                print("\n👋 Goodbye!")
                break

            # Process sentence
            if verbose:
                print(f"\n⚙️  Processing: {sentence}")
                print("-" * 60)

            try:
                result = run_pipeline(sentence)
                print_pipeline_result(result, verbose=verbose)
            except Exception as e:
                print(f"❌ Error during processing: {e}")
                import traceback
                traceback.print_exc()

            if verbose:
                print("\n" + "=" * 60)

        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break

        except Exception as e:
            print(f"\n❌ Error: {e}")
            print("Please try again.\n")


def main():
    """Entry point for the interactive CLI."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Luma Service/Reservation Booking - Interactive Mode",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--domain',
        default='service',
        choices=['service', 'reservation'],
        help='Domain type (default: service)'
    )
    parser.add_argument(
        '--timezone',
        default='UTC',
        help='Timezone for calendar binding (default: UTC)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed stage-by-stage output (default: show only final result)'
    )

    args = parser.parse_args()

    try:
        interactive_main(verbose=args.verbose)
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
