"""
Orchestration Layer

Control flow and decision making for conversation handling.

This package contains the orchestrator which handles:
- Message entry handling
- org_id + domain derivation
- catalog + tenant_context construction
- calling Luma
- contract validation
- branching on needs_clarification
- deciding outcomes based on plan status (NEEDS_CLARIFICATION, AWAITING_CONFIRMATION, READY)
- calling business execution functions

Subpackages:
- execution: Business execution actions (availability, booking, confirmation) and clients
- actions: Action handlers for booking operations
- api: Public API endpoints
- clients: Context clients (catalog, customer, organization)
- cache: Caching for catalog and organization data
- nlu: Natural language understanding integration
- session: Session state management
"""

