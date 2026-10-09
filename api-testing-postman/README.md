# API Testing with Postman

Request collections and run results for public REST APIs, captured with
Postman.

## What is covered

| Folder | API | What is exercised |
|---|---|---|
| `GitHub_Tasks/` | GitHub REST API | `GET` requests, environment configuration, and the run output. Shows header and auth setup, path/query variables, and how to read the response. |
| `Replit_Tasks/` | Replit API | `GET`, `POST` and `PATCH` against a workspace: create → read → update a resource, with the run results for each verb. |

## What this demonstrates

* **Environments over hardcoded values.** Base URL and credentials live in a
  Postman environment, not in the request body. `JoroFirstEnvironments.png` is
  the environment setup.
* **The full verb lifecycle.** `Replit_Tasks` is a create → read → update
  sequence, so state changes between requests are handled deliberately rather
  than accidentally.
* **Verification of the response.** Each run screenshot captures the status
  code and the response body, which is what turns "I sent a request" into a
  test result.
* **Read-only by design.** Every API touched here is a public developer API used
  within its own terms. No scraping, no bulk extraction, no credentials stored
  in the repository.

## Related material

* [`../dotnet-test-automation/`](../dotnet-test-automation/) — the same
  verification approach expressed as repeatable code (NUnit + Selenium).
* [`../ai-property-market/`](../ai-property-market/) — a pipeline built around
  public open data, with its source legality review in
  `collector/SOURCES_LEGAL.md`.

A QA training exercise at SoftUni. Written without AI assistance.