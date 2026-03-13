---
name: write-gundi-connector
description: Implement a Gundi data integration connector. Use when implementing Gundi connectors for pull actions, webhooks, or push actions with the Gundi connectors framework.
model: claude-opus-4-5-20251101
---

# Write Gundi Connector Skill

You are tasked with implementing a data integration (connector) using the Gundi connectors framework (actions runner template).

## Overview

**Framework Documentation**: Read [README.md](../../../README.md) to understand the Gundi connectors framework architecture.

This skill implements connectors that:
- **Pull data** from external APIs → Gundi (Pull Actions)
- **Receive data** pushed from external systems → Gundi (Webhooks)
- **Send data** from Gundi → external systems (Push Actions)

**Reference Documentation**:
- **Coding Standards**: [conventions.md](conventions.md) - PEP8, schemas, framework rules
- **Code Examples**: [examples.md](examples.md) - Copy-paste ready templates
- **Testing Guide**: [testing.md](testing.md) - Complete test patterns
- **Advanced Patterns**: [reference.md](reference.md) - State management, retry logic

---

## Required Information Gathering

Before implementing, gather information using the `AskUserQuestion` tool with multiple-choice options. This provides a better user experience while still allowing free-text responses via the automatic "Other" option.

### Question 1: Integration Direction & Type

Use `AskUserQuestion` with these options:

```
Question: "What is the goal of this integration?"
Header: "Direction"
Options:
- Label: "Pull Action"
  Description: "Pull data INTO Gundi from a third-party API on a schedule"
- Label: "Webhook Handler"
  Description: "Receive data PUSHED to Gundi via webhooks from external systems"
- Label: "Push Action"
  Description: "Send data FROM Gundi to an external system"
- Label: "Bidirectional"
  Description: "Multiple handlers needed (e.g., pull + push, or webhook + push)"
```

### Question 2: Data Types & Entities

Use `AskUserQuestion` with `multiSelect: true`:

```
Question: "What type of data will be processed? (Select all that apply)"
Header: "Data types"
multiSelect: true
Options:
- Label: "Observations"
  Description: "Tracking data: GPS positions, telemetry, location updates"
- Label: "Events"
  Description: "Incidents, alerts, patrol reports, sightings"
- Label: "Attachments"
  Description: "Images, documents, files associated with observations/events"
```

See [conventions.md](conventions.md#gundi-data-schemas) for schema details.

### Question 3: Authentication Method

Use `AskUserQuestion`:

```
Question: "What authentication method does the external API use?"
Header: "Auth method"
Options:
- Label: "API Key"
  Description: "Static API key passed in header or query parameter"
- Label: "OAuth 2.0"
  Description: "Token-based auth with refresh tokens"
- Label: "Basic Auth"
  Description: "Username and password credentials"
- Label: "No Auth"
  Description: "Public API with no authentication required"
```

### Question 4: Configuration Requirements

Use `AskUserQuestion` with `multiSelect: true`:

```
Question: "What settings should be configurable through the portal? (Select all that apply)"
Header: "Config"
multiSelect: true
Options:
- Label: "Lookback period"
  Description: "Number of days to look back for historical data (pull actions)"
- Label: "Filter parameters"
  Description: "API filters like device IDs, status, date ranges"
- Label: "Field mappings"
  Description: "Custom mapping between external fields and Gundi schema"
- Label: "Rate limits"
  Description: "Request throttling and batch size settings"
```

See [conventions.md](conventions.md#configuration-base-classes) for config model examples.

### Question 5: API Documentation

Use `AskUserQuestion`:

```
Question: "How would you like to provide API documentation?"
Header: "API docs"
Options:
- Label: "Paste examples"
  Description: "I'll paste sample request/response JSON in chat"
- Label: "Provide URL"
  Description: "I'll share a link to the API documentation"
- Label: "Upload file"
  Description: "I'll upload an API spec file (OpenAPI, Postman, etc.)"
- Label: "Describe verbally"
  Description: "I'll describe the API endpoints and data format"
```

**After receiving API docs, understand:**
- Request format (headers, query params, body structure)
- Response schema (JSON structure, pagination, error formats)
- Rate limits and retry logic requirements
- Data transformation needed between third-party format and Gundi entities

### Additional Questions (Ask as needed)

**Unmapped fields handling:**
```
Question: "How should we handle fields from the source API that don't map directly to Gundi schema fields?"
Header: "Extra fields"
Options:
- Label: "Include all (Recommended)"
  Description: "Pass unmapped fields in event_details (Events) or additional (Observations)"
- Label: "Discard"
  Description: "Ignore fields that don't have a direct Gundi mapping"
- Label: "Select specific"
  Description: "I'll specify which extra fields to include"
```

**Data filtering:**
```
Question: "Do you need to filter data based on field values or custom criteria before sending to Gundi?"
Header: "Filtering"
Options:
- Label: "No filtering"
  Description: "Send all data from the source API to Gundi"
- Label: "Yes, filter by field"
  Description: "Filter records based on specific field values (I'll explain the criteria)"
- Label: "Yes, custom logic"
  Description: "Apply custom filtering logic (I'll describe the rules)"
```

*If user selects a filtering option, follow up to get the specific filter criteria.*

**Scheduling (for pull actions):**
```
Question: "How often should the pull action run?"
Header: "Schedule"
Options:
- Label: "Every 5 minutes"
  Description: "High frequency for real-time tracking"
- Label: "Every hour"
  Description: "Standard frequency for most integrations"
- Label: "Every 6 hours"
  Description: "Low frequency for batch data"
- Label: "Daily"
  Description: "Once per day for daily reports/summaries"
```

**Error handling:**
```
Question: "How should the integration handle partial failures?"
Header: "Errors"
Options:
- Label: "Fail fast"
  Description: "Stop on first error, report failure"
- Label: "Graceful degradation (Recommended)"
  Description: "Continue processing other items, report partial success"
- Label: "Retry indefinitely"
  Description: "Keep retrying failed items until success"
```

---

## Implementation Workflow

1. **Gather all required information** (ask questions above!)
   - Integration type (pull/push/webhook)
   - Data types and schemas
   - Configuration requirements
   - API documentation

2. **Read existing boilerplate code** in `app/actions/` or `app/webhooks/`

3. **Create API client module** (if needed)
   - See [examples.md#api-client-implementation](examples.md#api-client-implementation)
   - Create client class with async context manager support
   - Define custom exceptions for different error scenarios
   - Implement error handling with `stamina` retry decorator
   - Add timeout configuration

4. **Define configuration models** in `configurations.py`
   - See [conventions.md#configuration-base-classes](conventions.md#configuration-base-classes)
   - For push actions: Create both `AuthenticateConfig` and push config
   - Use `ExecutableActionMixin` for auth configs
   - Add helpful descriptions and validation rules

5. **Implement handler function** in `handlers.py`
   - See [examples.md#handler-function-signatures](examples.md#handler-function-signatures)
   - For push actions: Implement both `action_auth()` and `action_push_*`
   - For pull actions: Implement state management for incremental pulls
   - Use activity loggers (`@activity_logger()` or `@webhook_activity_logger()`)
   - Let exceptions bubble to framework (see [reference.md#error-handling-best-practices](reference.md#error-handling-best-practices))
   - Add retry logic with `@stamina.retry()` for Gundi API calls
   - Transform data to Gundi schema

6. **Add state management** (for pull actions)
   - See [reference.md#state-management](reference.md#state-management)
   - Use `IntegrationStateManager` to track per-device state
   - Store `latest_device_timestamp` or pagination cursors
   - Update state after successful processing

7. **Add required dependencies** to `requirements.in`
   - See [conventions.md#common-dependencies](conventions.md#common-dependencies)
   - httpx, stamina, pytest, pytest-asyncio, respx, pyjq (if needed)

8. **Write comprehensive unit tests**
   - See [testing.md](testing.md) for complete guide
   - Create test fixtures in `conftest.py`
   - Write API client tests with respx mocking
   - Write action handler tests with mocked dependencies
   - Test all success and error scenarios

9. **Run all tests and verify they pass**
   - Run `pytest -v` to execute all tests
   - Ensure 100% of new tests pass
   - Fix any failing tests before proceeding

10. **Explain the implementation** to the user with examples

---

## Important Reminders

### Before Coding
- ✅ **ALWAYS use the `AskUserQuestion` tool** to gather requirements interactively
- ✅ **Present multiple-choice options** - users can click to select, or type custom answers via "Other"
- ✅ **Ask questions in batches** - group related questions (e.g., direction + data types) in a single `AskUserQuestion` call when appropriate
- ✅ **ALWAYS ask clarification questions BEFORE coding**
- ❌ **NEVER modify `app/services/`** (framework code)

### During Implementation
- ✅ Use async for I/O operations (`httpx`, not `requests`)
- ✅ Validate external data with Pydantic
- ✅ Use `@stamina.retry()` for sending data to Gundi
- ✅ Implement state management for pull actions
- ✅ Let exceptions bubble to framework (it handles logging)
- ✅ Transform external data to Gundi schema
- ✅ For push actions: Implement both `action_auth()` and push handler
- ✅ For push actions: Retrieve auth config via `integration.get_action_config("auth")`
- ✅ For multi-device pulls: Use graceful degradation

### Testing Requirements (CRITICAL)
- ✅ **ALWAYS write unit tests for ALL new code**
- ✅ **ALWAYS run `pytest -v` and ensure ALL tests pass**
- ✅ Test both success and error scenarios
- ✅ Use `respx` to mock HTTP requests
- ✅ Aim for high test coverage (API client: 100%)
- ❌ **NEVER skip testing** - untested code is incomplete

---

## Quick Reference

| Topic | File | Section |
|-------|------|---------|
| Framework Rules | [conventions.md](conventions.md) | Framework Rules |
| Data Schemas | [conventions.md](conventions.md#gundi-data-schemas) | Gundi Data Schemas |
| Handler Examples | [examples.md](examples.md#handler-function-signatures) | Handler Function Signatures |
| API Client Template | [examples.md](examples.md#api-client-implementation) | API Client Implementation |
| Testing Guide | [testing.md](testing.md) | Full Testing Guide |
| State Management | [reference.md](reference.md#state-management) | State Management |
| Retry Logic | [reference.md](reference.md#retry-strategy-with-stamina) | Retry Strategy |
| Error Handling | [reference.md](reference.md#error-handling-best-practices) | Error Handling |
| Pull vs Push | [reference.md](reference.md#key-differences-pull-vs-push-vs-webhook) | Key Differences |

---

For detailed patterns, see the reference files above. Start by asking the clarifying questions, then follow the implementation workflow step by step.
