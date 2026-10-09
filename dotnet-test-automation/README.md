# QA Automation — NUnit, Moq & Selenium WebDriver

Automated test suites written in C# / .NET 8, covering unit testing with
mocking, Selenium IDE recorder output, and end-to-end browser automation.

Written by Georgi Kirev — a QA training exercise at SoftUni, plus a project I
took on myself. No AI coding assistance was used here.

| Project | Type | Tests | Purpose |
|---|---|---|---|
| [e2e-idea-center](e2e-idea-center) | E2E (Selenium WebDriver 4 + NUnit) | 12 | Full CRUD lifecycle, navigation, authentication and negative paths on a live web application |
| [e2e-storyspoil-selenium-ide](e2e-storyspoil-selenium-ide) | E2E (Selenium IDE + WebDriver) | 10 code + 10 recorded | Recorded manual-style tests plus an equivalent code-based suite for the same application |
| [unit-tests-nunit-moq](unit-tests-nunit-moq) | Unit (NUnit + Moq) | 27 | Isolated service tests using mocked repositories |

**49 code-based tests in total** (27 unit + 22 end-to-end), plus 10 Selenium IDE
recordings of the same coverage in a different style.

---

## What the suites demonstrate

* **Unit testing with mocking** - `Mock<T>` for repository and notifier
  dependencies, `Verify`/`VerifyNoOtherCalls` for interaction assertions,
  `Throws<T>` for behaviour under failure, and `[TestCase]` for
  boundary-value analysis.
* **Test design** - tests are grouped into meaningful scenarios, assert one
  behaviour each, use `Assert.Multiple` to report related checks together, and
  clean up the data they create so the suite can be run repeatedly.
* **Selenium WebDriver** - explicit waits instead of sleeps, resilient locators,
  alert handling, headless execution, and configuration kept out of the code.
* **Environment isolation** - no credentials in the repository; every value is
  read from environment variables with a documented fallback.

---

## Requirements

* [.NET SDK 8.0 or newer](https://dotnet.microsoft.com/download)
* Google Chrome (the Selenium suites run headless by default)

## Running the tests

```bash
# Unit tests (fast, no browser required)
dotnet test "unit-tests-nunit-moq/ItemManagement/ItemManagement.sln"
dotnet test "unit-tests-nunit-moq/NotificationApplication/NotificationApp.sln"

# End-to-end suites (need the target application to be reachable)
dotnet test e2e-idea-center/IdeaCenterPrep.sln
dotnet test "e2e-storyspoil-selenium-ide/StorySpoilSeleniumTests/StorySpoilSeleniumTests.sln"
```

### Watching the browser run

Both end-to-end suites default to headless Chrome. Set the matching variable to
`false` to see the browser:

```bash
set IDEACENTER_HEADLESS=false
set STORYSPOIL_HEADLESS=false
```

---

## Notes

* The end-to-end suites run against a **shared, externally hosted application**.
  Tests 1-6 of each suite walk through one ordered CRUD scenario and delete the
  data they create, but a failed run can still leave a record behind.
* ChromeDriver is **not** pinned to a version. Selenium Manager resolves the
  driver that matches the locally installed Chrome, which keeps the suites
  working after a browser update.