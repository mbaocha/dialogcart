# Configuration-driven booking subjects

Service bookings may send a flat `booking_subject` snapshot when
`BOOKING_SUBJECT_ENABLED` is enabled. The supported configuration contract is:

```yaml
- name: registration_number       # Commerce flat JSON key
  type: text                       # supported: text or enum
  role: booking_subject
  required: true                   # collection and execution prerequisite
  availability_criteria: false     # independently controls availability identity
```

Multiple subject fields are supported. Required enum/text fields participate in
normal missing-slot planning. Optional enum/text fields are captured only when
the user voluntarily supplies them; `prompt_if_missing` is not generally
effective for enum/text fields.

The snapshot is service-only. Reservation requests do not send it. The Core
gate is disabled by default and accepts `1`, `true`, `yes`, or `on`. Commerce's
compatible generic-write gate must be enabled before Core sending is enabled.

Catalog subject fields currently produce an ID-only value under the YAML field
name. That behavior is characterized but deferred and is not a certified
product contract. Native number and boolean entity declarations are unsupported.
