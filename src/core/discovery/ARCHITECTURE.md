# Discovery Architecture

Domain-neutral engine for **Search → Navigator → Selector**.

Discovery is infrastructure. Business domains plug in via lightweight
policies. Availability remains the current production implementation and
becomes the first Discovery consumer in Phase 3.

---

## Flow

```text
Search.search(provider=...)
  → TrustedResult
Navigator.present(policy=...)
  → PresentedWindow
Navigator.browse(BrowseIntent)
  → PresentedWindow
Selector.resolve(policy=...)
  → SelectionResult
```

Planner and orchestration consume outcomes; they do not own search trust,
navigation mechanics, or selection resolution.

---

## Public API

| Component | Methods |
|---|---|
| `Search` | `search(request, provider, *, existing=None)` |
| `Navigator` | `present(trusted)`, `browse(intent, ...)` |
| `Selector` | `resolve(request, *, window, trusted=None)` |

Domain adapters: `SearchProvider`, `NavigationPolicy`, `SelectionPolicy`.

---

## Component responsibilities

### Search

- Receive `SearchRequest`
- Compute search identity via `SearchProvider.identity`
- Reuse `TrustedResult` when identity still matches
- Invoke `SearchProvider.search` only when necessary
- Build and return `TrustedResult`

Does **not** present, navigate, select, or set planner policy.

### Navigator

- Derive `PresentedWindow` from `TrustedResult` via `NavigationPolicy`
- Handle browse movement
- Preserve navigation state
- Detect exhaustion (`last_moved`)

Does **not** search, bind selections, or set planner policy.
Grouping and ordering live in `NavigationPolicy`.

### Selector

- Resolve ambiguous choices against `PresentedWindow` only
- Resolve explicit choices against `TrustedResult` when trusted
- Produce `SelectionResult`

Does **not** navigate, search, book, or make planner decisions.
Matching lives in `SelectionPolicy`.

---

## Public models

| Model | Role |
|---|---|
| `SearchRequest` | Opaque search criteria |
| `TrustedResult` | Authoritative search outcome for the current search identity |
| `PresentedWindow` | Navigable subset currently shown to the user |
| `BrowseIntent` | User request to move within presented results |
| `SelectionRequest` | Opaque selection criteria |
| `SelectionResult` | Resolved user choice for planner consumption |

All models describe **conversation semantics**, not a specific domain.

---

## Domain policies

| Policy | Owns |
|---|---|
| `SearchProvider` | Identity + domain search execution |
| `NavigationPolicy` | Initial window + advance / grouping / ordering |
| `SelectionPolicy` | Explicitness + item matching |

Discovery owns the workflow. Policies own domain behaviour.

---

## State ownership

| Owner | State |
|---|---|
| Search | `TrustedResult`, search identity, provider invocation |
| Navigator | `PresentedWindow`, navigation state |
| Selector | `SelectionResult` |

Discovery does **not** own sessions, planner state, booking state, or
execution state.

---

## Phase 3

Availability plugs in with:

- `AvailabilityProvider` (`SearchProvider`)
- `AvailabilityNavigationPolicy` (`NavigationPolicy`)
- `AvailabilitySelectionPolicy` (`SelectionPolicy`)

Discovery performs the workflow; Availability supplies domain behaviour only.
