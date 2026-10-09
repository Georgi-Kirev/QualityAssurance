# QA Portfolio — Georgi Kirev

Automated and manual testing work, built as a portfolio to demonstrate how I
approach quality assurance: designing test cases, finding real defects,
automating the repetitive parts, and keeping the suites runnable by anyone.

This repository has two distinct parts, and they are not written the same way.

**[`ai-property-market/`](ai-property-market/) is an AI-assisted project.** I
designed it, specified the architecture, defined every behaviour that had to be
protected and every defect that had to be kept from coming back, and reviewed
the output at each step. The code was written with an AI coding assistant
working from those instructions. Where a test exists, there is a reason — most
are regression tests for defects that actually happened.

**The rest of the repository is my own work.** The C# automation suites, the
manual test cases and the Postman collections are exercises from my QA
training at SoftUni plus projects I took on myself. That code is mine.

**731 automated tests in total: 682 in Python (pytest), 49 in C# (NUnit).**

| | |
|---|---|
| **Author** | Georgi Ivanov Kirev (Хасково, България) |
| **Focus** | Test automation and test design |
| **Stack** | Python 3.10+, pytest, C# / .NET 8, NUnit, Moq, Selenium WebDriver |
| **CI** | GitHub Actions runs every push (see the badge below) |
| **License** | MIT |

[![Automated Tests](https://github.com/Georgi-Kirev/QualityAssurance/actions/workflows/tests.yml/badge.svg)](https://github.com/Georgi-Kirev/QualityAssurance/actions/workflows/tests.yml)

---

## Repository layout

| Folder | Origin | What it is |
|---|---|---|
| [`ai-property-market/`](ai-property-market/) | **AI-assisted, my project** | A Python data pipeline (normalize → deduplicate → match → export) plus a Flask search API, covered by 682 automated tests. Start here. |
| [`dotnet-test-automation/`](dotnet-test-automation/) | My work — SoftUni exercise + own initiative | 49 automated tests in C#: unit testing with NUnit and Moq, Selenium IDE recordings, and Selenium WebDriver end-to-end suites. |
| [`manual-tests-web-app-scrum/`](manual-tests-web-app-scrum/) | My work — SoftUni exercise | Manual test cases and test runs for a web application under test (Scrum board), with evidence screenshots. |
| [`manual-test-cases/`](manual-test-cases/) | My work — my own template and examples | Test case templates and suites — my reusable QA_Manual_Test_Case_Template, plus worked examples for SauceDemo and Facebook login. |
| [`api-testing-postman/`](api-testing-postman/) | My work — SoftUni exercise | API testing in Postman against public REST APIs, with request and run-result screenshots. |

Supporting material at the repository root: manual QA notes
(`QAManualЗаписки.xlsx`) and a Jira bug report example (`Small_Jira_Example.png`).

---

## Start here: the flagship project

```bash
cd ai-property-market
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

python main.py --tests          # 682 tests, no network, under 10 seconds
python main.py --demo           # sample data -> pipeline -> dashboard
```

It has to run offline, which makes it a good portfolio piece: the whole suite
passes with the network switched off, and a dedicated meta-test enforces that
condition. See [`ai-property-market/README.md`](ai-property-market/README.md) for
the architecture, the design decisions, and a table of the real bugs each
regression test protects against.

## The C# suites

```bash
# Unit tests, no browser needed
dotnet test "dotnet-test-automation/unit-tests-nunit-moq/ItemManagement/ItemManagement.sln"
dotnet test "dotnet-test-automation/unit-tests-nunit-moq/NotificationApplication/NotificationApp.sln"

# End-to-end suites, need the target application to be reachable
dotnet test dotnet-test-automation/e2e-idea-center/IdeaCenterPrep.sln
dotnet test "dotnet-test-automation/e2e-storyspoil-selenium-ide/StorySpoilSeleniumTests/StorySpoilSeleniumTests.sln"
```

Details per suite are in [`dotnet-test-automation/README.md`](dotnet-test-automation/README.md).

---

## What the automated tests demonstrate

* **Regression tests with a cause.** Tests such as
  `test_iter_json_records_preserves_numbers` exist because a real defect
  silently turned every property price into `0`; `test_location_is_not_an_address`
  because treating geometry as an address produced 25 825 false matches. The
  commit that introduced the bug is the reason the test exists.
* **Test isolation.** Fixtures redirect every module's write paths to
  `tmp_path`, so no test touches real data. A meta-test verifies this, because
  one test once wrote into the live `storage_raw/` folder and broke the pipeline
  without raising a single error.
* **Mocking and interaction assertions.** `Mock<T>` for repository and
  notifier dependencies, plus `Verify`, `VerifyNoOtherCalls` and `Throws<T>`
  for behaviour under failure.
* **Stable Selenium practices.** Explicit `WebDriverWait` instead of sleeps,
  resilient locators, JavaScript-based absence checks, headless by default,
  and configuration through environment variables with no credentials in the
  repository.
* **Boundary-value analysis.** `[TestCase]` parameters cover the edges rather
  than a single nominal value.
* **Reproducible results.** Deterministic property IDs across runs, atomic
  writes, and a network policy with a single source of truth.

## CI

[`.github/workflows/tests.yml`](.github/workflows/tests.yml) runs on every push
and pull request:

1. `pytest` — the full 682-test Python suite, plus the offline pipeline itself.
2. `dotnet test` — both NUnit/Moq unit solutions (27 tests).
3. `dotnet build` — both Selenium WebDriver solutions, so they cannot rot.

The end-to-end Selenium suites are compiled but not executed in CI, because they
need a reachable application. They are meant to be run locally.

## A note on AI assistance

AI assistance applies to **`ai-property-market/` only**. The rest of this
repository — every C# suite, every manual test case, the Postman collections —
was written by me without it.

For the AI-assisted project the workflow was: I decided what the software should
do and which failures were unacceptable, described each defect when a test was
written to prevent it, and reviewed the generated code against the module it
covers. The AI was good at the mechanical work and bad at deciding what actually
needs testing, which is the part I kept.

The test counts and test names are specific on purpose — they are meant to be
read by someone evaluating how I test, not just to report a green run.

## License

MIT — see [`LICENSE`](LICENSE).

Author: **Georgi Ivanov Kirev**, Хасково, България, 2026.
