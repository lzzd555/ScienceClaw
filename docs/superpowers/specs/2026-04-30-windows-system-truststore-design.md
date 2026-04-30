# Windows System Trust Store for Backend HTTPS

## Background

API Monitor MCP tool tests are executed by the local backend process, not by Postman. The failing request path is:

```text
Frontend test action
-> backend.route.mcp.test_api_monitor_tool
-> backend.deepagent.mcp_runtime.ApiMonitorMcpRuntime.call_tool
-> httpx.AsyncClient
-> target HTTPS API
```

On Windows, Postman can successfully call the same API with certificate verification enabled because it uses the Windows system certificate store. Python HTTP clients in the backend, including `httpx`, currently use Python/OpenSSL certificate resolution, commonly backed by `certifi`, and therefore do not automatically trust certificates installed in Windows Trusted Root Certification Authorities.

This causes `CERTIFICATE_VERIFY_FAILED` for APIs whose certificate chain is trusted by Windows but not by Python's default CA bundle.

## Goals

- Allow local Windows backend runs to use the Windows system certificate store for outbound HTTPS requests.
- Fix API Monitor MCP tool testing when target APIs rely on enterprise, self-signed, or privately issued CA certificates already trusted by Windows.
- Apply the behavior early enough that `httpx.AsyncClient` instances created across the backend share the same TLS trust behavior.
- Keep explicit user-provided CA bundle environment variables working.
- Avoid disabling TLS verification.

## Non-Goals

- Do not add `verify=False`, `NODE_TLS_REJECT_UNAUTHORIZED=0`, or equivalent insecure bypasses.
- Do not require users to manually export Windows certificates into a PEM bundle for the default Windows local workflow.
- Do not change Docker certificate mounting behavior in this design.
- Do not redesign API Monitor MCP request execution.
- Do not force all platforms to change TLS behavior without an escape hatch.

## Recommended Approach

Add the Python `truststore` package and enable it during backend bootstrap when configured to do so. `truststore` allows Python TLS clients to use the operating system trust store:

- Windows: Windows CryptoAPI system certificate store
- macOS: system Keychain
- Linux: system CA paths through OpenSSL

The primary target is Windows local development and packaged desktop usage.

## Configuration

Add a backend environment variable:

```text
RPA_USE_SYSTEM_TRUSTSTORE=auto
```

Supported values:

- `auto`: enable on Windows local backend runs when no explicit CA bundle override is present.
- `true`: try to enable `truststore` on any platform.
- `false`: do not enable `truststore`.

Default:

```text
auto
```

Explicit CA bundle variables must take precedence:

```text
SSL_CERT_FILE
REQUESTS_CA_BUNDLE
CURL_CA_BUNDLE
```

If any of these are set, `auto` mode should not inject `truststore`, because the user has already chosen a certificate bundle. In `true` mode, the backend may still attempt injection, but should log that explicit CA variables are present.

## Backend Design

Create a small TLS bootstrap helper, for example:

```text
backend/tls_trust.py
```

Responsibilities:

- Read `RPA_USE_SYSTEM_TRUSTSTORE`.
- Detect the current platform with `sys.platform`.
- Detect explicit CA bundle environment variables.
- Decide whether `truststore.inject_into_ssl()` should run.
- Log the resulting TLS trust strategy.
- Never log certificate contents or sensitive environment values.

Suggested decision table:

| Mode | Platform | Explicit CA env present | Action |
| --- | --- | --- | --- |
| `false` | any | any | Do not inject |
| `auto` | Windows | no | Inject `truststore` |
| `auto` | Windows | yes | Do not inject; respect explicit CA bundle |
| `auto` | non-Windows | any | Do not inject by default |
| `true` | any supported platform | any | Attempt injection |

Call the helper at the top of `backend/main.py`, before route modules or other backend modules create outbound HTTP clients.

Example placement:

```python
from backend.tls_trust import configure_tls_trust

configure_tls_trust()
```

This must happen before code creates long-lived `httpx.AsyncClient` instances. It is acceptable if some modules import `httpx` before injection; the important boundary is before client construction and outbound requests.

## Dependency Change

Add to backend requirements:

```text
truststore>=0.10.0
```

The project uses Python 3.13 for backend, which satisfies `truststore`'s Python version requirement.

## Error Handling

`truststore` setup should be best-effort:

- If injection succeeds, log an info message:
  ```text
  TLS trust store: system via truststore
  ```
- If `RPA_USE_SYSTEM_TRUSTSTORE=false`, log at debug level:
  ```text
  TLS trust store: Python default
  ```
- If `auto` skips because explicit CA env vars are present, log an info message:
  ```text
  TLS trust store: explicit CA bundle environment detected; system truststore not injected
  ```
- If injection fails, log a warning and continue startup:
  ```text
  TLS trust store: truststore injection failed; falling back to Python default
  ```

Backend startup should not fail solely because `truststore` could not be injected. A failed target API call should still surface the original HTTPS error to the user.

## Data Flow

After this change, the API Monitor MCP test flow on Windows becomes:

```text
Frontend test action
-> FastAPI backend process starts
-> configure_tls_trust injects truststore
-> ApiMonitorMcpRuntime creates httpx.AsyncClient
-> Python SSL uses Windows system certificate store
-> target HTTPS API certificate chain validates
```

This aligns Python backend trust behavior with Postman's Windows system certificate behavior.

## Testing

Unit tests:

- `RPA_USE_SYSTEM_TRUSTSTORE=false` does not import or inject `truststore`.
- `RPA_USE_SYSTEM_TRUSTSTORE=auto` on Windows with no CA env calls the injection path.
- `RPA_USE_SYSTEM_TRUSTSTORE=auto` on Windows with `SSL_CERT_FILE` set skips injection.
- `RPA_USE_SYSTEM_TRUSTSTORE=auto` on Linux/macOS skips injection.
- `RPA_USE_SYSTEM_TRUSTSTORE=true` attempts injection on supported platforms.
- Injection failure is logged and does not raise.

Integration/manual verification:

1. On Windows, install the enterprise or private CA into Windows Trusted Root Certification Authorities.
2. Confirm Postman succeeds with certificate verification enabled.
3. Start backend locally with no `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, or `CURL_CA_BUNDLE`.
4. Confirm backend logs `TLS trust store: system via truststore`.
5. Run the API Monitor MCP tool test against the same HTTPS endpoint.
6. Confirm the request no longer fails with `CERTIFICATE_VERIFY_FAILED`.

Regression check:

- Verify model connection tests and other backend `httpx.AsyncClient` call sites still work.
- Verify explicit CA bundle workflows still work when `SSL_CERT_FILE` is set.

## Risks and Mitigations

- Risk: TLS behavior changes for all backend outbound HTTPS calls on Windows.
  Mitigation: gate with `RPA_USE_SYSTEM_TRUSTSTORE`, default only to Windows `auto`, and preserve explicit CA bundle overrides.

- Risk: `truststore` behaves differently from `certifi` for some public endpoints.
  Mitigation: allow `RPA_USE_SYSTEM_TRUSTSTORE=false` to return to Python default behavior.

- Risk: injection happens too late.
  Mitigation: call `configure_tls_trust()` near the top of `backend/main.py`, before route imports that may create clients.

- Risk: packaged desktop startup does not pass the new environment variable.
  Mitigation: default `auto` handles Windows without requiring a new desktop setting.

## Rollout

1. Add `truststore` to backend dependencies.
2. Add `backend/tls_trust.py`.
3. Invoke TLS bootstrap early from `backend/main.py`.
4. Add unit tests for the decision table.
5. Manually verify on Windows with the API Monitor MCP tool test.
6. Document the new `RPA_USE_SYSTEM_TRUSTSTORE` environment variable in local development guidance.

