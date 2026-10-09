# Manual Testing — Web Application (Scrum Board)

Manual test cases and full test run evidence for a web application managed on a
Scrum board. This is the manual counterpart to the automated suites in
[`../dotnet-test-automation/`](../dotnet-test-automation/).

## Environment

| | |
|---|---|
| Application under test | OpenCart demo — <https://demo.opencart.com/> |
| Test management | Qase.io |
| OS | Windows 10 |
| Browser | Chrome 141.0.7390.123 (64-bit) |

## Contents

| Path | What it shows |
|---|---|
| `WebApp_Test_Documentation.xlsx` | The test documentation: scope, environments, test data, and the case list. |
| `Test_Cases_and_Suites.png` | How the cases are grouped into suites and how they map to the requirements. |
| `1.Suite_Checkout_&_Cart_Tests/` | Evidence for the checkout and cart suite: the suite itself, the overall run, and the individual run. |
| `2.General_Functional_Tests/` | Evidence for the general functional suite, including the run that was in progress when the test environment became unavailable. |

## The two suites

**Checkout & Cart** — the highest-value area of any shop: adding to cart,
quantity changes, removing items, coupon codes, checkout steps, and the order
confirmation.

**General Functional** — navigation, search, product page, categories, account
and contact flows.

## What this demonstrates

* **Traceability.** Cases are written against documented requirements, and the
  grouping is visible rather than implied.
* **Evidence, not claims.** Each suite ships the actual run result, not just the
  planned cases. Pass/fail counts and defect links are recorded per run.
* **Honest reporting.** `2.General_Functional_Tests/2.Continuing work after
  site crash.png` documents a suite that could not be completed because the
  environment went down. A test report that hides interruptions is worth less
  than one that shows them.

## Note on credentials

`ReadMe.txt` in this folder records the test account used for the runs. It was
created for this testing exercise only, and the application data behind it has
since been removed, so the account no longer works.

A QA training exercise at SoftUni. Written without AI assistance.