# Unit Testing with Mocking — NUnit & Moq

Two independent solutions demonstrating unit testing with mocked dependencies.
Both run in well under a second and need no database, no network and no browser.

```bash
dotnet test ItemManagement/ItemManagement.sln
dotnet test NotificationApplication/NotificationApp.sln
```

---

## 1. ItemManagement

A small console CRUD application layered as
`ItemManagementApp` → `ItemManagementLib` → Entity Framework. The
`ItemService` under test is exercised with a mocked `IItemRepository`, so the
service logic is verified in complete isolation from the database.

**27 tests across the two suites** — the 16 ItemManagement tests cover:

* the happy path for add, get, update and delete;
* the missing-entity branch of `UpdateItem` and `GetItemById`;
* an empty repository result;
* propagation of a repository exception to the caller;
* `ValidateItemName` as a boundary-value test — `0123456789` (10 characters) is
  accepted, `01234567890` (11) is not;
* `VerifyNoOtherCalls` to prove the service talks to the repository exactly once.

## 2. NotificationApp

`NotificationService` decides whether and how a user should be notified, with
both the user repository and the notifier mocked.

The 11 tests cover the happy path and every rejection branch — empty message,
unknown user, inactive user — asserting both the exception type and message, and
that the notifier was never called. `NotifyUser_CalledTwice_ShouldNotifyOncePerCall`
confirms the service is stateless between calls.

---

## Patterns used

| Pattern | Where |
|---|---|
| `Mock<T>` in `[SetUp]` | both suites |
| `Setup().Returns()` | arranging return values |
| `Setup().Throws<T>()` | arranging failures |
| `Verify(..., Times.Once/Never)` | interaction assertions |
| `VerifyNoOtherCalls()` | proving no unexpected collaboration |
| `Assert.Throws<T>()` | exception behaviour |
| `[TestCase]` | boundary values and parameterized cases |
| `Is.SameAs` | reference equality where identity matters |

## Test design

Every test follows **Arrange – Act – Assert**, keeps the Arrange block explicit
even when a test looks trivial, and asserts a single behaviour so that a failure
points straight at the code that broke.