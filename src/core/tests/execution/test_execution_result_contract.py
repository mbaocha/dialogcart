from core.execution.result import normalize_execution_result
from core.rendering.llm_renderer import LlmRenderRequest, _build_user_message


def test_booking_result_resolves_numeric_service_id_to_catalog_label():
    result = normalize_execution_result(
        {
            "action": "CONFIRM_APPOINTMENT",
            "intent_name": "CREATE_APPOINTMENT",
            "slots": {
                "organization_id": 2,
                "service_id": 26,
                "start_time": "2026-08-17T09:00:00+01:00",
            },
            "_entity_schema": {
                "version": 1,
                "fields": [
                    {
                        "name": "service",
                        "type": "catalog",
                        "role": "bookable_item",
                        "catalog": {"Executive Oil Change": 26},
                    }
                ],
            },
        },
        {"booking": {"id": 13, "booking_code": "ORG2-000013"}},
    )

    assert result["subject"]["service_name"] == "Executive Oil Change"


def test_normalizes_availability_result():
    result = normalize_execution_result(
        {
            "action": "SEARCH_AVAILABILITY",
            "intent_name": "CREATE_APPOINTMENT",
            "slots": {
                "organization_id": 7,
                "customer_id": 12,
                "service_id": "premium haircut",
            },
        },
        {
            "type": "availability",
            "status": "success",
            "slots": [{"starts_at": "2026-07-16T09:00:00Z"}],
        },
    )

    assert result["schema_version"] == 1
    assert result["status"] == "succeeded"
    assert result["subject"]["kind"] == "availability"
    assert result["subject"]["service_name"] == "premium haircut"
    assert result["availability"]["slots"] == [
        {"starts_at": "2026-07-16T09:00:00Z"}
    ]


def test_normalizes_booking_and_reference_evidence():
    result = normalize_execution_result(
        {
            "action": "CONFIRM_APPOINTMENT",
            "intent_name": "CREATE_APPOINTMENT",
            "slots": {"organization_id": 7, "customer_id": 12},
        },
        {
            "status": "EXECUTED",
            "booking": {
                "id": 42,
                "booking_code": "BK-42",
                "service_name": "Premium Haircut",
                "starts_at": "2026-07-16T09:00:00Z",
                "ends_at": "2026-07-16T09:30:00Z",
            },
        },
    )

    assert result["refs"] == {
        "organization_id": 7,
        "customer_id": 12,
        "booking_id": 42,
        "booking_code": "BK-42",
    }
    assert result["subject"]["kind"] == "booking"
    assert result["subject"]["starts_at"] == "2026-07-16T09:00:00Z"


def test_normalizes_cancelled_response_as_successful_cancellation():
    result = normalize_execution_result(
        {
            "action": "CONFIRM_CANCELLATION",
            "intent_name": "CANCEL_BOOKING",
            "slots": {"organization_id": 7, "booking_id": 42},
        },
        {
            "status": "cancelled",
            "booking_id": 42,
            "booking_code": "BK-42",
            "cancellation": {"status": "cancelled"},
        },
    )

    assert result["status"] == "succeeded"
    assert result["subject"]["kind"] == "cancellation"
    assert result["refs"]["booking_code"] == "BK-42"


def test_llm_request_consumes_complete_execution_evidence():
    execution = normalize_execution_result(
        {
            "action": "CONFIRM_APPOINTMENT",
            "intent_name": "CREATE_APPOINTMENT",
            "slots": {"organization_id": 7},
        },
        {
            "status": "EXECUTED",
            "booking": {"id": 42, "booking_code": "BK-42"},
        },
    )

    message = _build_user_message(
        LlmRenderRequest(
            render_instruction="Confirm the result.",
            facts={"execution": execution},
        )
    )

    assert '"schema_version": 1' in message
    assert '"booking_code": "BK-42"' in message
