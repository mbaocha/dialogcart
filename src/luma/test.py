#!/usr/bin/env python3
"""
Interactive Pipeline Test Script

Tests the entire Luma pipeline:
1. Entity Extraction (EntityMatcher)
2. Intent Resolution (ReservationIntentResolver)
3. Structural Interpretation (interpret_structure)
4. Appointment Grouping (group_appointment)
5. Semantic Resolution (resolve_semantics)
6. Calendar Binding (bind_calendar)

Usage:
    python -m luma.test
    python dialogcart/src/luma/test.py
"""
from luma.calendar.calendar_binder import bind_calendar
from luma.resolution.semantic_resolver import resolve_semantics
from luma.grouping.appointment_grouper import group_appointment
from luma.structure.interpreter import interpret_structure
from luma.grouping.reservation_intent_resolver import ReservationIntentResolver
from luma.extraction.matcher import EntityMatcher
import argparse
import types
import contextlib
import io
import sys
import json
import importlib.util
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
import requests

# CRITICAL: Import spaCy BEFORE setting up luma module structure
# The issue: Python searches the script's directory (src/luma/) first when importing
# So when spaCy imports 'calendar', Python finds luma/calendar/ instead of stdlib calendar
#
# Workaround: Temporarily prevent luma.calendar from being imported by manipulating
# the import system. We'll add a custom import hook that redirects 'calendar' to stdlib.
script_dir = Path(__file__).parent.resolve()  # luma/
src_dir = script_dir.parent  # src/
src_path = str(src_dir)

# Custom import hook to redirect 'calendar' to stdlib version


class StdlibCalendarImportHook:
    """Import hook that ensures 'calendar' imports the stdlib version."""

    def find_spec(self, name, path, target=None):
        if name == 'calendar':
            # Force import from stdlib
            import importlib.util
            import os
            stdlib_path = os.path.dirname(os.__file__)
            calendar_path = os.path.join(stdlib_path, 'calendar.py')
            if os.path.exists(calendar_path):
                return importlib.util.spec_from_file_location('calendar', calendar_path)
        return None


# Install the import hook BEFORE importing spaCy
sys.meta_path.insert(0, StdlibCalendarImportHook())

# Now import spaCy - this should work without circular import issues
SPACY_VERIFIED = False
SPACY_NLP = None

try:
    import spacy
    SPACY_VERIFIED = True
    try:
        SPACY_NLP = spacy.load("en_core_web_sm")  # Verify model is available
    except OSError as e:
        print(f"⚠️  Warning: spaCy model not found: {e}")
        print("   Download with: python -m spacy download en_core_web_sm")
        SPACY_VERIFIED = False
except ImportError as e:
    SPACY_VERIFIED = False
    error_msg = str(e)
    print(f"⚠️  Warning: spaCy not available: {error_msg}")
    if "zipp" in error_msg.lower():
        print("   Missing dependency 'zipp'. Install with:")
        print("   pip install zipp")
        print("   Or reinstall spaCy: pip install --upgrade spacy")
    else:
        print("   Install with: pip install spacy")
        print("   Then download model: python -m spacy download en_core_web_sm")
except Exception as e:
    SPACY_VERIFIED = False
    print(f"⚠️  Warning: spaCy import failed: {e}")
    print("   Try: pip install --upgrade spacy")

# Add src directory to path AFTER stdlib imports and spaCy
# (script_dir and src_path are already defined above)
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# Mock problematic imports BEFORE importing luma modules
# This prevents numpy dependency issues when luma.__init__ is imported


class MockNERModel:
    pass


# Create minimal package structure BEFORE any imports

# Create luma package
luma_pkg = types.ModuleType('luma')
luma_pkg.__path__ = [str(script_dir)]
luma_pkg.__file__ = str(script_dir / "__init__.py")
sys.modules['luma'] = luma_pkg

# Create and register subpackages with __path__
# IMPORTANT: Do NOT register 'calendar' here to avoid conflicts with stdlib calendar module
# Instead, register it only when needed, or use a different approach
for subpkg_name in ['extraction', 'grouping', 'structure', 'resolution', 'classification', 'config']:
    subpkg_path = script_dir / subpkg_name
    if subpkg_path.exists():
        subpkg_mod = types.ModuleType(f'luma.{subpkg_name}')
        subpkg_mod.__path__ = [str(subpkg_path)]
        subpkg_mod.__file__ = str(subpkg_path / "__init__.py")
        sys.modules[f'luma.{subpkg_name}'] = subpkg_mod

# Register calendar package AFTER all standard library imports are done
# This prevents Python from accidentally importing luma.calendar when stdlib needs calendar
calendar_subpkg_path = script_dir / 'calendar'
if calendar_subpkg_path.exists():
    calendar_mod = types.ModuleType('luma.calendar')
    calendar_mod.__path__ = [str(calendar_subpkg_path)]
    calendar_mod.__file__ = str(calendar_subpkg_path / "__init__.py")
    sys.modules['luma.calendar'] = calendar_mod

# Mock classification to avoid numpy
if 'luma.classification' not in sys.modules:
    sys.modules['luma.classification'] = types.ModuleType(
        'luma.classification')
sys.modules['luma.classification'].NERModel = MockNERModel

# Mock config
if 'luma.config' not in sys.modules:
    sys.modules['luma.config'] = types.ModuleType('luma.config')
sys.modules['luma.config'].debug_print = lambda *args, **kwargs: None
sys.modules['luma.config'].DEBUG_ENABLED = False

# Now import pipeline components
# These imports will work because we've set up the package structure

# Try zoneinfo first (Python 3.9+), fallback to pytz
try:
    from zoneinfo import ZoneInfo
    try:
        _ = ZoneInfo("UTC")
        ZONEINFO_AVAILABLE = True
    except Exception:
        ZONEINFO_AVAILABLE = False
except ImportError:
    ZONEINFO_AVAILABLE = False

try:
    import pytz
    PYTZ_AVAILABLE = True
except ImportError:
    PYTZ_AVAILABLE = False
    pytz = None


def _localize_datetime(dt: datetime, tz_str: str) -> datetime:
    """Localize datetime to timezone."""
    if ZONEINFO_AVAILABLE:
        try:
            tz = ZoneInfo(tz_str)
            return dt.replace(tzinfo=tz)
        except Exception:
            from datetime import timezone
            return dt.replace(tzinfo=timezone.utc)
    elif PYTZ_AVAILABLE:
        tz = pytz.timezone(tz_str)
        return tz.localize(dt)
    else:
        from datetime import timezone
        return dt.replace(tzinfo=timezone.utc)


def find_normalization_dir() -> Path:
    """Find the normalization directory containing global JSON file."""
    from luma.extraction.entity_loading import get_global_json_path

    # Try multiple locations
    possible_paths = [
        script_dir.parent.parent / "store" / "normalization",
        script_dir / "store" / "normalization",
        Path(__file__).parent.parent.parent / "store" / "normalization",
    ]

    for path in possible_paths:
        if path.exists():
            try:
                # This will raise FileNotFoundError if the configured version doesn't exist
                get_global_json_path(path)
                return path
            except FileNotFoundError:
                continue

    # If not found, raise error with helpful message
    from luma.config import config
    raise FileNotFoundError(
        f"Could not find normalization directory with global.{config.GLOBAL_JSON_VERSION}.json. "
        f"Tried: {[str(p) for p in possible_paths]}"
    )


def run_full_pipeline(
    sentence: str,
    domain: str = "service",
    timezone: str = "UTC",
    now: datetime = None
) -> Dict[str, Any]:
    """
    Run the complete Luma pipeline.

    Args:
        sentence: User input sentence
        domain: "service" or "reservation"
        timezone: Timezone string (e.g., "UTC", "America/New_York")
        now: Current datetime (defaults to now)

    Returns:
        Dictionary with results from each pipeline stage
    """
    if now is None:
        now = datetime.now()
        now = _localize_datetime(now, timezone)

    # Find normalization directory
    normalization_dir = find_normalization_dir()
    # Use any JSON file in normalization dir
    entity_file = str(normalization_dir / "101.v1.json")

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
    print("\n" + "="*70)
    print("STAGE 1: ENTITY EXTRACTION")
    print("="*70)
    print(f"Input: {sentence}")
    print(f"Domain: {domain}")

    try:
        matcher = EntityMatcher(domain=domain, entity_file=entity_file)
        extraction_result = matcher.extract_with_parameterization(sentence)
        results["stages"]["extraction"] = extraction_result

        print(f"\nExtracted Entities:")
        print(
            f"  Services: {len(extraction_result.get('business_categories') or extraction_result.get('service_families', []))}")
        business_categories = extraction_result.get(
            'business_categories') or extraction_result.get('service_families', [])
        for svc in business_categories:
            print(f"    - {svc.get('text')} ({svc.get('canonical')})")

        print(f"  Dates: {len(extraction_result.get('dates', []))}")
        for d in extraction_result.get('dates', []):
            print(f"    - {d.get('text')}")

        print(
            f"  Absolute Dates: {len(extraction_result.get('dates_absolute', []))}")
        for d in extraction_result.get('dates_absolute', []):
            print(f"    - {d.get('text')}")

        print(f"  Times: {len(extraction_result.get('times', []))}")
        for t in extraction_result.get('times', []):
            print(f"    - {t.get('text')}")

        print(
            f"  Time Windows: {len(extraction_result.get('time_windows', []))}")
        for tw in extraction_result.get('time_windows', []):
            print(f"    - {tw.get('text')}")

        print(f"  Durations: {len(extraction_result.get('durations', []))}")
        for dur in extraction_result.get('durations', []):
            print(f"    - {dur.get('text')}")

        print(
            f"\nParameterized Sentence: {extraction_result.get('psentence')}")

    except Exception as e:
        error_msg = str(e)
        print(f"ERROR in extraction: {error_msg}")
        if "spacy" in error_msg.lower() or "spacy" in str(type(e)).lower():
            print("\n💡 Tip: Install spaCy with:")
            print("   pip install spacy")
            print("   python -m spacy download en_core_web_sm")
        results["stages"]["extraction"] = {"error": error_msg}
        return results

    # Stage 2: Intent Resolution
    print("\n" + "="*70)
    print("STAGE 2: INTENT RESOLUTION")
    print("="*70)

    try:
        intent_resolver = ReservationIntentResolver()
        intent, confidence = intent_resolver.resolve_intent(
            sentence,
            extraction_result
        )
        results["stages"]["intent"] = {
            "intent": intent,
            "confidence": confidence
        }

        print(f"Intent: {intent}")
        print(f"Confidence: {confidence}")

    except Exception as e:
        print(f"ERROR in intent resolution: {e}")
        results["stages"]["intent"] = {"error": str(e)}
        return results

    # Stage 3: Structural Interpretation
    print("\n" + "="*70)
    print("STAGE 3: STRUCTURAL INTERPRETATION")
    print("="*70)

    try:
        psentence = extraction_result.get('psentence', '')
        structure = interpret_structure(psentence, extraction_result)
        results["stages"]["structure"] = structure.to_dict()["structure"]

        print(f"Booking Count: {structure.booking_count}")
        print(f"Service Scope: {structure.service_scope}")
        print(f"Time Scope: {structure.time_scope}")
        print(f"Date Scope: {structure.date_scope}")
        print(f"Time Type: {structure.time_type}")
        print(f"Has Duration: {structure.has_duration}")
        print(f"Needs Clarification: {structure.needs_clarification}")

    except Exception as e:
        print(f"ERROR in structural interpretation: {e}")
        results["stages"]["structure"] = {"error": str(e)}
        return results

    # Stage 4: Appointment Grouping
    print("\n" + "="*70)
    print("STAGE 4: APPOINTMENT GROUPING")
    print("="*70)

    try:
        intent_result = {
            "intent": intent,
            "booking": {
                "services": extraction_result.get('business_categories') or extraction_result.get('service_families', []),
                "date_ref": None,
                "time_ref": None,
                "duration": None
            },
            "structure": structure.to_dict()["structure"]
        }

        # Extract date/time references
        dates = extraction_result.get('dates', [])
        dates_absolute = extraction_result.get('dates_absolute', [])
        times = extraction_result.get('times', [])
        time_windows = extraction_result.get('time_windows', [])
        durations = extraction_result.get('durations', [])

        if dates_absolute:
            intent_result["booking"]["date_ref"] = dates_absolute[0].get(
                'text')
        elif dates:
            intent_result["booking"]["date_ref"] = dates[0].get('text')

        if times:
            intent_result["booking"]["time_ref"] = times[0].get('text')
        elif time_windows:
            intent_result["booking"]["time_ref"] = time_windows[0].get('text')

        if durations:
            intent_result["booking"]["duration"] = durations[0]

        grouped_result = group_appointment(extraction_result, structure)
        results["stages"]["grouping"] = grouped_result

        print(f"Intent: {grouped_result.get('intent')}")
        print(f"Status: {grouped_result.get('status')}")
        print(
            f"Services: {len(grouped_result.get('booking', {}).get('services', []))}")
        print(f"Date Ref: {grouped_result.get('booking', {}).get('date_ref')}")
        print(f"Time Ref: {grouped_result.get('booking', {}).get('time_ref')}")
        print(f"Duration: {grouped_result.get('booking', {}).get('duration')}")
        if grouped_result.get('reason'):
            print(f"Reason: {grouped_result.get('reason')}")

    except Exception as e:
        print(f"ERROR in grouping: {e}")
        results["stages"]["grouping"] = {"error": str(e)}
        return results

    # Stage 5: Semantic Resolution
    print("\n" + "="*70)
    print("STAGE 5: SEMANTIC RESOLUTION")
    print("="*70)

    try:
        semantic_result = resolve_semantics(grouped_result, extraction_result)
        results["stages"]["semantic"] = semantic_result.to_dict()

        resolved = semantic_result.resolved_booking
        print(f"Date Mode: {resolved.get('date_mode')}")
        print(f"Date Refs: {resolved.get('date_refs')}")
        print(f"Time Mode: {resolved.get('time_mode')}")
        print(f"Time Refs: {resolved.get('time_refs')}")
        print(f"Duration: {resolved.get('duration')}")
        print(f"Needs Clarification: {semantic_result.needs_clarification}")
        if semantic_result.clarification:
            print(f"Reason: {semantic_result.clarification.reason.value}")

    except Exception as e:
        print(f"ERROR in semantic resolution: {e}")
        results["stages"]["semantic"] = {"error": str(e)}
        return results

    # Stage 6: Calendar Binding
    print("\n" + "="*70)
    print("STAGE 6: CALENDAR BINDING")
    print("="*70)

    try:
        calendar_result = bind_calendar(
            semantic_result,
            now,
            timezone,
            intent=intent,
            entities=extraction_result  # Pass entities for time-window bias rule
        )
        results["stages"]["calendar"] = calendar_result.to_dict()

        booking = calendar_result.calendar_booking
        if booking:
            print(f"Date Range: {booking.get('date_range')}")
            print(f"Time Range: {booking.get('time_range')}")
            print(f"Datetime Range: {booking.get('datetime_range')}")
            print(f"Duration: {booking.get('duration')}")
        else:
            print("No calendar binding (intent-guarded)")

        print(f"Needs Clarification: {calendar_result.needs_clarification}")
        if calendar_result.clarification:
            print(f"Reason: {calendar_result.clarification.reason.value}")

    except Exception as e:
        print(f"ERROR in calendar binding: {e}")
        results["stages"]["calendar"] = {"error": str(e)}
        return results

    return results


def call_api(
    sentence: str,
    domain: str = "service",
    timezone: str = "UTC",
    api_base: str = "http://localhost:9001/resolve",
    tenant_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Call Luma HTTP API (/resolve) instead of running locally.
    """
    payload: Dict[str, Any] = {
        "user_id": "cli-user",
        "text": sentence,
        "domain": domain,
        "timezone": timezone,
    }
    if tenant_context:
        payload["tenant_context"] = tenant_context

    response = requests.post(
        api_base,
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    try:
        body = response.json()
    except Exception:
        body = {"success": False, "error": "Non-JSON response",
                "text": response.text}

    return {
        "http_status": response.status_code,
        "request": payload,
        "response": body,
    }


def print_json_summary(results: Dict[str, Any]):
    """Print a JSON summary of the results."""
    print("\n" + "="*70)
    print("JSON SUMMARY")
    print("="*70)
    print(json.dumps(results, indent=2, default=str))


def interactive_mode():
    """Run interactive testing mode."""
    print("="*70)
    print("LUMA PIPELINE - INTERACTIVE TEST MODE")
    print("="*70)
    print("\nEnter sentences to test the full pipeline.")
    print("\nCommands:")
    print("  'quit' or 'exit' - Exit")
    print("  'json' - Toggle JSON output")
    print("  'domain <service|reservation>' - Set domain")
    print("  'timezone <tz>' - Set timezone (e.g., UTC, America/New_York)")
    print("\nExample sentences:")
    print("  book me in for haircut tomorrow morning at 10.30")
    print("  reserve a standard room from 8th dec to 15th dec")
    print("  what times are available for haircut tomorrow?")
    print()

    show_json = False
    domain = "service"
    timezone = "UTC"

    while True:
        try:
            user_input = input("\n> ").strip()

            if not user_input:
                continue

            if user_input.lower() in ['quit', 'exit', 'q']:
                print("Goodbye!")
                break

            if user_input.lower() == 'json':
                show_json = not show_json
                print(f"JSON output: {'ON' if show_json else 'OFF'}")
                continue

            if user_input.lower().startswith('domain '):
                new_domain = user_input.split(' ', 1)[1].strip()
                if new_domain in ['service', 'reservation']:
                    domain = new_domain
                    print(f"Domain set to: {domain}")
                else:
                    print(
                        f"Invalid domain: {new_domain}. Must be 'service' or 'reservation'")
                continue

            if user_input.lower().startswith('timezone '):
                new_tz = user_input.split(' ', 1)[1].strip()
                timezone = new_tz
                print(f"Timezone set to: {timezone}")
                continue

            # Run pipeline
            results = run_full_pipeline(
                user_input, domain=domain, timezone=timezone)

            if show_json:
                print_json_summary(results)

        except KeyboardInterrupt:
            print("\n\nInterrupted. Goodbye!")
            break
        except Exception as e:
            print(f"\nERROR: {e}")
            import traceback
            traceback.print_exc()


def example_tests():
    """
    Run example test cases (50) covering supported intents.

    Prints only failures; summarizes total/pass/fail.
    """
    test_cases = [
        {"text": "book premium haircut tomorrow at 9am", "expected_intent": "CREATE_BOOKING",
            "require_service": True, "require_datetime": True, "expected_services": ["beauty_and_wellness.haircut"]},
        {"text": "need a massage this friday at 5pm", "expected_intent": "CREATE_BOOKING",
            "require_service": True, "expected_services": ["beauty_and_wellness.massage"]},
        {"text": "schedule a spa treatment next monday afternoon", "expected_intent": "CREATE_BOOKING",
            "require_service": True, "expected_services": ["beauty_and_wellness.spa_treatment"]},
        {"text": "book manicure next week morning", "expected_intent": "CREATE_BOOKING",
            "require_service": True, "expected_services": ["beauty_and_wellness.manicure"]},
        {"text": "book beard trim today at 3pm", "expected_intent": "CREATE_BOOKING", "require_service": True,
            "require_datetime": True, "expected_services": ["beauty_and_wellness.beard_grooming"]},
        {"text": "can I book premium haircut at 2pm tomorrow", "expected_intent": "CREATE_BOOKING",
            "require_service": True, "expected_services": ["beauty_and_wellness.haircut"]},
        {"text": "want an appointment on wednesday at 10am",
            "expected_intent": "CREATE_BOOKING"},
        {"text": "book two services for tomorrow evening",
            "expected_intent": "CREATE_BOOKING"},
        {"text": "I want to reserve a room for July 4 to July 6", "expected_intent": "CREATE_BOOKING",
            "domain": "reservation", "require_datetime": True, "expected_services": ["hospitality.room"]},
        {"text": "need a hotel room next weekend", "expected_intent": "CREATE_BOOKING",
            "domain": "reservation", "expected_services": ["hospitality.room"]},
        {"text": "find availability for haircut tomorrow morning", "expected_intent": "AVAILABILITY",
            "require_service": True, "require_datetime": True, "expected_services": ["beauty_and_wellness.haircut"]},
        {"text": "what times are open for massage on friday", "expected_intent": "AVAILABILITY",
            "require_service": True, "expected_services": ["beauty_and_wellness.massage"]},
        {"text": "do you have slots for nail service today",
            "expected_intent": "AVAILABILITY", "require_service": True},
        {"text": "what services do you offer", "expected_intent": "DISCOVERY"},
        {"text": "list your rooms", "expected_intent": "DISCOVERY",
            "domain": "reservation"},
        {"text": "how much is premium haircut", "expected_intent": "QUOTE",
            "require_service": True, "expected_services": ["beauty_and_wellness.haircut"]},
        {"text": "price for massage please", "expected_intent": "QUOTE",
            "require_service": True, "expected_services": ["beauty_and_wellness.massage"]},
        {"text": "recommend a spa treatment", "expected_intent": "RECOMMENDATION"},
        {"text": "suggest a good room type for 2 adults",
            "expected_intent": "RECOMMENDATION", "domain": "reservation"},
        {"text": "tell me more about premium haircut", "expected_intent": "DETAILS",
            "require_service": True, "expected_services": ["beauty_and_wellness.haircut"]},
        {"text": "I want to cancel booking ORG1-000123",
            "expected_intent": "CANCEL_BOOKING"},
        {"text": "cancel my booking ORG1-000124 now",
            "expected_intent": "CANCEL_BOOKING"},
        {"text": "update booking ORG1-000125 to 5pm",
            "expected_intent": "MODIFY_BOOKING"},
        {"text": "reschedule booking ORG1-000126 to tomorrow 9am",
            "expected_intent": "MODIFY_BOOKING"},
        {"text": "change my reservation ORG1-000127 to next weekend",
            "expected_intent": "MODIFY_BOOKING", "domain": "reservation"},
        {"text": "what's the status of booking ORG1-000128",
            "expected_intent": "BOOKING_INQUIRY"},
        {"text": "show booking ORG1-000129 details",
            "expected_intent": "BOOKING_INQUIRY"},
        {"text": "payment for booking ORG1-000130", "expected_intent": "PAYMENT"},
        {"text": "pay for my booking ORG1-000131", "expected_intent": "PAYMENT"},
        {"text": "confirm my booking ORG1-000132", "expected_intent": "UNKNOWN"},
        {"text": "I want to move it later", "expected_intent": "MODIFY_BOOKING"},
        {"text": "do I need to pay deposit", "expected_intent": "PAYMENT"},
        {"text": "what is the cancellation policy", "expected_intent": "DETAILS"},
        {"text": "can I add breakfast to my stay",
            "expected_intent": "MODIFY_BOOKING", "domain": "reservation"},
        {"text": "I want to change guest count on my reservation",
            "expected_intent": "MODIFY_BOOKING", "domain": "reservation"},
        {"text": "can I book another slot tomorrow",
            "expected_intent": "CREATE_BOOKING"},
        {"text": "is there availability for facial today evening", "expected_intent": "AVAILABILITY",
            "require_service": True, "expected_services": ["beauty_and_wellness.facial"]},
        {"text": "book haircut at 6ish tonight", "expected_intent": "CREATE_BOOKING",
            "require_service": True, "expected_services": ["beauty_and_wellness.haircut"]},
        {"text": "need a room with two beds", "expected_intent": "CREATE_BOOKING",
            "domain": "reservation", "expected_services": ["hospitality.room"]},
        {"text": "how late are you open", "expected_intent": "DETAILS"},
        {"text": "do you support refunds", "expected_intent": "DETAILS"},
        {"text": "I want to book on the 25th at noon",
            "expected_intent": "CREATE_BOOKING"},
        {"text": "schedule beard trim at 11am saturday", "expected_intent": "CREATE_BOOKING",
            "require_service": True, "expected_services": ["beauty_and_wellness.beard_grooming"]},
        {"text": "move my appointment earlier",
            "expected_intent": "MODIFY_BOOKING"},
        {"text": "cancel reservation code ORG1-000200",
            "expected_intent": "CANCEL_BOOKING", "domain": "reservation"},
        {"text": "show me my reservation ORG1-000201",
            "expected_intent": "BOOKING_INQUIRY", "domain": "reservation"},
        {"text": "pay balance for ORG1-000202", "expected_intent": "PAYMENT"},
        {"text": "what is the payment status for ORG1-000203",
            "expected_intent": "PAYMENT"},
        {"text": "I need a quote for deep tissue massage",
            "expected_intent": "QUOTE", "require_service": True},
        {"text": "recommend something relaxing",
            "expected_intent": "RECOMMENDATION"},
        {"text": "what's available tomorrow afternoon",
            "expected_intent": "AVAILABILITY"},
        {"text": "is any room free for tonight",
            "expected_intent": "AVAILABILITY", "domain": "reservation"},
    ]

    total = len(test_cases)
    failures = []
    passes = []

    for i, tc in enumerate(test_cases, 1):
        sentence = tc["text"]
        expected_intent = tc["expected_intent"]
        require_service = tc.get("require_service", False)
        require_datetime = tc.get("require_datetime", False)
        expected_services = set(tc.get("expected_services", []))

        # Domain: explicit override or heuristic
        if tc.get("domain"):
            domain = tc["domain"]
        else:
            lower = sentence.lower()
            domain = "reservation" if any(
                key in lower for key in ["room", "hotel", "reservation", "stay", "bed"]
            ) else "service"

        try:
            # Suppress verbose pipeline output; keep only our summary
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                results = run_full_pipeline(
                    sentence, domain=domain, timezone="UTC")
            stages = results.get("stages", {}) if results else {}
            intent = stages.get("intent", {}).get("intent")
            grouping_booking = stages.get("grouping", {}).get(
                "booking", {}) if isinstance(stages.get("grouping"), dict) else {}
            calendar_booking = stages.get("calendar", {}).get(
                "calendar_booking", {}) if isinstance(stages.get("calendar"), dict) else {}
            services = []
            for svc in grouping_booking.get("services", []) or []:
                if isinstance(svc, dict):
                    services.append(svc.get("canonical") or svc.get("text"))
            datetime_range = calendar_booking.get(
                "datetime_range") or grouping_booking.get("datetime_range")

            errors = []
            if intent != expected_intent:
                errors.append(
                    f"intent mismatch: expected={expected_intent}, got={intent}")
            if require_service and not services:
                errors.append("expected at least one service, got none")
            if require_datetime and not datetime_range:
                errors.append("expected datetime_range, got none")
            if expected_services:
                normalized = {str(s) for s in services if s}
                missing = set(expected_services) - normalized
                if missing:
                    errors.append(
                        f"expected services {expected_services}, got {normalized or 'none'}")

            if errors:
                raise AssertionError("; ".join(errors))

            passes.append((i, sentence, intent, services, datetime_range))
        except Exception as exc:  # noqa: BLE001
            failures.append((i, sentence, str(exc)))

    passed = total - len(failures)

    print("=" * 70)
    print(
        f"EXAMPLE TESTS SUMMARY: total={total}, passed={passed}, failed={len(failures)}")
    print("=" * 70)

    if passes:
        print("\nPasses (intent + slots):")
        for idx, sent, intent, services, dtr in passes:
            svc_str = ", ".join([s for s in services if s]
                                ) if services else "-"
            dtr_str = dtr if dtr else "-"
            print(
                f"- {idx}: intent={intent or '-'} | services={svc_str} | datetime_range={dtr_str}")

    if failures:
        print("\nFailures (index: sentence -> error):")
        for idx, sent, err in failures:
            print(f"- {idx}: {sent} -> {err}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test Luma pipeline interactively or via HTTP API")
    parser.add_argument(
        '--examples',
        action='store_true',
        help='Run example test cases'
    )
    parser.add_argument(
        '--sentence',
        type=str,
        help='Test a single sentence'
    )
    parser.add_argument(
        '--domain',
        type=str,
        default='service',
        choices=['service', 'reservation'],
        help='Domain to use (default: service)'
    )
    parser.add_argument(
        '--timezone',
        type=str,
        default='UTC',
        help='Timezone (default: UTC)'
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='Output JSON summary'
    )
    parser.add_argument(
        '--api',
        action='store_true',
        help='Call HTTP API /resolve instead of running locally'
    )
    parser.add_argument(
        '--api-base',
        type=str,
        default='http://localhost:9001/resolve',
        help='API base URL for /resolve (default: http://localhost:9001/resolve)'
    )
    parser.add_argument(
        '--tenant-domain',
        type=str,
        help='DEPRECATED: Not used by API. Use --tenant-context instead.'
    )
    parser.add_argument(
        '-t', '--tenant-context',
        type=str,
        help='Optional tenant_context JSON string (e.g., \'{"aliases":{"premium haircut":"haircut"}}\')'
    )

    args = parser.parse_args()

    if args.examples:
        example_tests()
    elif args.sentence:
        if args.api:
            tenant_ctx = None
            if args.tenant_context:
                try:
                    tenant_ctx = json.loads(args.tenant_context)
                except Exception as exc:  # noqa: BLE001
                    print(f"Invalid tenant_context JSON: {exc}")
                    sys.exit(1)

            api_result = call_api(
                sentence=args.sentence,
                domain=args.domain,
                timezone=args.timezone,
                api_base=args.api_base,
                tenant_context=tenant_ctx,
            )
            print_json_summary(api_result)
        else:
            results = run_full_pipeline(
                args.sentence,
                domain=args.domain,
                timezone=args.timezone
            )
            # Return only the final calendar binding result
            final_result = {
                "input": results["input"],
                "result": results["stages"]["calendar"]
            }
            print_json_summary(final_result)
    else:
        interactive_mode()


if __name__ == "__main__":
    main()
