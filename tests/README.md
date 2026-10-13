# Tests

From the repository root, using the project's configured virtual environment:

```sh
python -m pip install -e . -r tests/requirements.txt
python -m pytest tests
```

To run the endpoint suite with line and branch coverage:

```sh
python -m pytest tests/pytrain_api --cov=src.pytrain_api.endpoints --cov-branch --cov-report=term-missing
```

## Scope and isolation

- `test_endpoint_auth.py`: API keys, JWT claims/signatures/expiry, token issuance,
  lifecycle cleanup, public documentation and icons, and exception handlers.
- `test_endpoint_system.py`: system command aliases, CLI dispatch, failures,
  and route metadata helpers.
- `test_endpoint_commands.py`: every component command route, both legacy and
  mobile aliases, exact command arguments, defaults, options, and backend errors.
- `test_endpoint_reads.py`: list/detail responses, filtering, normalization,
  missing state, and engine product information.
- `test_endpoint_validation.py`: authentication, ID boundaries, invalid query
  parameters, malformed/missing JSON, discriminated command models, and rejection
  before dispatch.
- `test_pytrain_api.py`: registered route inventory, security dependencies,
  metadata, and OpenAPI inclusion.

Tests send in-process HTTP requests through the real FastAPI router, validation,
authentication, response models, and exception handlers. Shared fixtures replace
only PyTrain/hardware boundaries, provide deterministic test-only credentials,
and restore configuration and dependency overrides after each test. Normal
requests do not start the application's service-discovery lifespan; lifecycle
tests explicitly exercise it with mocked service registration. No live layout,
API server, serial device, or credentials are required.

## Recorded behavior and regressions

The suite includes regressions for mobile train-speed requests awaiting their
handler and route-fire responses formatting integer addresses correctly.

Characterization tests also record existing HTTP 500 responses for malformed or
expired tokens and non-string `SERVER` claims on public `/version`, and dispatch
failures on debug, echo, and stop-all system commands. These behaviors are not a
recommendation for the API contract; if error handling is improved, update those
explicit tests with the intended status and response body.