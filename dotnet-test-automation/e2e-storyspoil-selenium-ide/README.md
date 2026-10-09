# Story Spoil — Selenium IDE & Selenium WebDriver

End-to-end tests for the **"Story Spoil"** web application, written twice:

* `The Story Spoil Web App.side` — recorded with **Selenium IDE**
* `StorySpoilSeleniumTests` — the equivalent suite in **C# / NUnit / Selenium WebDriver**

---

## Running the C# suite

```bash
dotnet test StorySpoilSeleniumTests/StorySpoilSeleniumTests.sln
```

The suite runs headless by default and completes in roughly 10 seconds.

## Configuration

No credentials are stored in the repository. Every value can be overridden
through an environment variable:

| Variable | Default |
|---|---|
| `STORYSPOIL_BASEURL` | `http://144.91.123.158:100` |
| `STORYSPOIL_USERNAME` | `Gogo` |
| `STORYSPOIL_EMAIL` | `Gogo123@abv.eu` |
| `STORYSPOIL_FIRSTNAME` | `Georgi` |
| `STORYSPOIL_MIDDLENAME` | `Naimov` |
| `STORYSPOIL_LASTNAME` | `Petkov` |
| `STORYSPOIL_PASSWORD` | `Gnp123456` |
| `STORYSPOIL_HEADLESS` | `true` |

## Covered functionality

| # | Test | What it verifies |
|---|---|---|
| 1 | `CreateStorySpoilerWithInvalidDataTest` | Submitting an empty form keeps the user on the page and shows *"Unable to add this spoiler!"* |
| 2 | `CreateRandomStorySpoilerTest` | A new spoiler is created and appears at the top of the list |
| 3 | `EditLastCreatedStorySpoilerTitleTest` | The edit form is pre-filled, and saving changes the title but not the description |
| 4 | `DeleteLastCreatedStorySpoilerTest` | Deleting removes exactly one entry from the list |
| 5 | `TryToEditNonExistentStorySpoilerTest` | Editing an unknown id reports *"No such spoiler!"* |
| 6 | `TryToDeleteNonExistentStorySpoilerTest` | Deleting an unknown id reports *"No such spoiler!"* |
| 7 | `CreateStorySpoilerWithMaximumLengthTitleTest` | Boundary value — a title of exactly 70 characters is accepted, then cleaned up |
| 8 | `LogOutRemovesTheSessionTest` | Logging out clears the session and hides authenticated navigation |
| 9 | `LoginWithInvalidPasswordShowsErrorTest` | A wrong password is rejected with *"Unable to sign in!"* |
| 10 | `RegisterWithAlreadyTakenEmailTest` | Re-registering an existing e-mail is rejected with *"Email already taken!"* |

Tests 1-4 form one ordered scenario (create → edit → delete). Tests 5-10 are
independent.

## Selenium IDE suite

`The Story Spoil Web App.side` contains the recorded manual-style version of the
same coverage, using the recording's default target URL. Note that a few of the
recorded tests assume that at least one spoiler already exists in the list.

## Notes on the implementation

* **No `Thread.Sleep`.** All timing is handled with `WebDriverWait`, so the suite
  is both faster and stable.
* **Locator notes.** The home page mixes static marketing sections with real
  spoilers under the same `section#scroll` selector, so spoilers are filtered by
  the presence of their per-item *Edit* link. Navigation links are located by
  `href` because their visible text is rendered uppercase through CSS.
* **Deep links.** The application does not scroll its window, so entries far
  down the list cannot be reached with a mouse click. The *Edit* and *Delete*
  links are therefore followed directly by their `href`.