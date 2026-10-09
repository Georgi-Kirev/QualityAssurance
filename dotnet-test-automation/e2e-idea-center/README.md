# Idea Center — Selenium WebDriver End-to-End Suite

End-to-end regression tests for the **"Idea Center"** web application, written
in C# with NUnit and Selenium WebDriver 4.

## Running the suite

```bash
dotnet test IdeaCenterPrep.sln
```

The suite runs headless by default and completes in roughly 25 seconds.

## Configuration

No credentials are stored in the repository. Every value can be overridden
through an environment variable:

| Variable | Default |
|---|---|
| `IDEACENTER_BASEURL` | `http://144.91.123.158:82` |
| `IDEACENTER_EMAIL` | `ivo1234@gmail.com` |
| `IDEACENTER_PASSWORD` | `ivo1234` |
| `IDEACENTER_HEADLESS` | `true` |

Set `IDEACENTER_HEADLESS=false` to watch the tests run in a visible browser.

## Covered functionality

| # | Test | What it verifies |
|---|---|---|
| 1 | `CreateIdeaWithInvalidDataTest` | Submitting an empty form keeps the user on the creation page and shows *"Unable to create new Idea!"* |
| 2 | `CreateRandomIdeaTest` | A new idea is created and the user is redirected to *My Ideas* |
| 3 | `ViewLastCreatedIdeaTest` | The details page shows the title of the newly created idea |
| 4 | `EditLastCreatedIdeaTitleTest` | Editing the title is reflected on the details page |
| 5 | `EditIdeaDescriptionTest` | Editing the description is reflected on the list page |
| 6 | `DeleteLastIdeaTest` | Deleting removes exactly one card from the list |
| 7 | `MyIdeasPageShowsTheSignedInUserNavigationTest` | Authenticated navigation links are present |
| 8 | `EditUnknownIdeaDoesNotOpenTheEditFormTest` | An unknown idea id does not render the edit form |
| 9 | `DeleteUnknownIdeaDoesNotDeleteAnyIdeaTest` | An unknown idea id leaves the existing ideas untouched |
| 10 | `LogOutRemovesTheSessionTest` | Logging out hides authenticated navigation |
| 11 | `MyIdeasRedirectsAnonymousVisitorToSignInTest` | An anonymous visitor is redirected to sign-in with a `ReturnUrl` |
| 12 | `SignInWithInvalidPasswordShowsErrorTest` | A wrong password is rejected with *"Unable to sign in!"* |

Tests 1-6 form one ordered CRUD scenario (create → view → edit → delete).
Tests 7-12 are independent.

## Reference material

`Selenium_Cheat_Sheet_Exam.txt` is a personal locator reference covering CSS and
XPath strategies, link text, waits, and the most common locator mistakes.

## Notes on the implementation

* **No `Thread.Sleep`.** All timing is handled with `WebDriverWait`.
* **Negative assertions use JavaScript.** Checking that an element is *absent*
  through `FindElements` would block for the full implicit wait on every poll;
  `ElementExists` answers immediately.
* **Setup is idempotent.** The account is reused across runs, so the suite does
  not try to register it again on every execution.