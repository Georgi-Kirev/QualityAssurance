using OpenQA.Selenium;
using OpenQA.Selenium.Support.UI;
using System;
using System.Collections.Generic;
using System.Linq;

namespace StorySpoilSeleniumTests
{
    /// <summary>
    /// End-to-end regression suite for the "Story Spoil" web application.
    ///
    /// Target application and test account are configured through
    /// <see cref="TestSettings"/> (environment variables), so no credentials
    /// are hard-coded in the repository.
    ///
    /// Tests 1-4 form one ordered scenario: a spoiler is created, edited and
    /// finally deleted again, so each step builds on the data created by the
    /// previous one. NUnit guarantees that order through the [Order]
    /// attributes and a single shared browser created in [OneTimeSetUp].
    /// Tests 5-9 are independent and exercise the remaining functionality.
    /// </summary>
    [TestFixture]
    [NonParallelizable]
    public class StorySpoilTests
    {
        private IWebDriver driver = null!;
        private WebDriverWait wait = null!;

        private const string TitlePrefix = "Title";
        private static string createdTitle = string.Empty;
        private static string createdDescription = string.Empty;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            driver = TestSettings.CreateDriver();
            wait = new WebDriverWait(driver, TimeSpan.FromSeconds(20));

            // The account usually exists already from a previous run, so
            // registration is best-effort and the login below does the real work.
            TryRegister();
            Login();
        }

        [OneTimeTearDown]
        public void OneTimeTearDown()
        {
            try
            {
                driver?.Quit();
                driver?.Dispose();
            }
            catch (WebDriverException)
            {
                // The browser may already be gone - teardown must never fail on that.
            }
        }

        // -------------------------------------------------------------------
        // Ordered scenario: create -> edit -> delete
        // -------------------------------------------------------------------

        [Test, Order(1)]
        public void CreateStorySpoilerWithInvalidDataTest()
        {
            NavigateTo(TestSettings.BaseUrl + "/Story/Add");

            driver.FindElement(By.CssSelector("button[type='submit']")).Click();

            wait.Until(d => d.Url.Contains("/Story/Add"));

            Assert.Multiple(() =>
            {
                Assert.That(driver.Url, Is.EqualTo(TestSettings.BaseUrl + "/Story/Add"),
                    "The user should stay on the creation page when the form is invalid");
                Assert.That(GetValidationSummary(), Does.Contain("Unable to add this spoiler!"),
                    "The summary validation message should be displayed");
            });
        }

        [Test, Order(2)]
        public void CreateRandomStorySpoilerTest()
        {
            createdTitle = TitlePrefix + GenerateRandomString(8);
            createdDescription = "Description" + GenerateRandomString(8);

            NavigateTo(TestSettings.BaseUrl + "/Story/Add");

            driver.FindElement(By.Id("title")).SendKeys(createdTitle);
            driver.FindElement(By.Id("description")).SendKeys(createdDescription);
            driver.FindElement(By.CssSelector("button[type='submit']")).Click();

            wait.Until(d => !d.Url.Contains("/Story/Add"));

            var spoilers = GetSpoilers();
            Assert.That(spoilers.Count, Is.GreaterThan(0),
                "At least the newly created spoiler should be listed on the home page");
            Assert.That(GetTitle(spoilers.Last()), Is.EqualTo(createdTitle),
                "The last spoiler in the list should be the one just created");
        }

        [Test, Order(3)]
        public void EditLastCreatedStorySpoilerTitleTest()
        {
            NavigateTo(TestSettings.BaseUrl);

            var lastSpoiler = GetSpoilers().Last();
            Assert.That(GetTitle(lastSpoiler), Does.StartWith(TitlePrefix),
                "Precondition: the newest spoiler must be the one created by the previous test");

            // The application's layout does not scroll the window, so entries deep
            // in the list cannot be reached with a mouse click. The Edit link is
            // therefore followed directly - the behaviour under test is the Edit
            // page itself, not the anchor that points at it.
            NavigateTo(GetActionLink(lastSpoiler, "/Story/Edit"));
            wait.Until(d => d.Url.Contains("/Story/Edit"));

            Assert.That(driver.FindElement(By.Id("title")).GetAttribute("value")?.Trim(),
                Is.EqualTo(createdTitle),
                "The edit form should be pre-filled with the current title");

            createdTitle = "Edited" + GenerateRandomString(8);

            var titleInput = driver.FindElement(By.Id("title"));
            titleInput.Clear();
            titleInput.SendKeys(createdTitle);
            driver.FindElement(By.CssSelector("button[type='submit']")).Click();

            wait.Until(d => !d.Url.Contains("/Story/Edit"));

            var updated = GetSpoilers().Last();
            Assert.Multiple(() =>
            {
                Assert.That(GetTitle(updated), Is.EqualTo(createdTitle),
                    "The spoiler title should show the edited value");
                Assert.That(updated.FindElement(By.CssSelector("p.flex-lg-wrap")).Text.Trim(),
                    Is.EqualTo(createdDescription),
                    "Editing the title must not change the description");
            });
        }

        [Test, Order(4)]
        public void DeleteLastCreatedStorySpoilerTest()
        {
            NavigateTo(TestSettings.BaseUrl);

            var spoilersBefore = GetSpoilers();
            var countBefore = spoilersBefore.Count;
            var titleToDelete = GetTitle(spoilersBefore.Last());

            // "Delete" is a plain link that removes the spoiler straight away -
            // there is no confirmation form to submit. It is followed directly
            // for the same scrolling reason as the Edit link above.
            NavigateTo(GetActionLink(spoilersBefore.Last(), "/Story/Delete"));

            wait.Until((_) =>
            {
                var titles = GetAllTitles();
                return titles.Count == countBefore - 1 && !titles.Contains(titleToDelete);
            });

            var titlesAfter = GetAllTitles();
            Assert.Multiple(() =>
            {
                Assert.That(titlesAfter, Has.Count.EqualTo(countBefore - 1),
                    "Deleting a spoiler should remove exactly one entry from the list");
                Assert.That(titlesAfter, Does.Not.Contain(titleToDelete),
                    "The deleted spoiler should no longer be listed");
            });
        }

        // -------------------------------------------------------------------
        // Error handling
        // -------------------------------------------------------------------

        [Test, Order(5)]
        public void TryToEditNonExistentStorySpoilerTest()
        {
            AssertPageReportsMissingSpoiler("/Story/Edit");
        }

        [Test, Order(6)]
        public void TryToDeleteNonExistentStorySpoilerTest()
        {
            AssertPageReportsMissingSpoiler("/Story/Delete");
        }

        [Test, Order(7)]
        public void CreateStorySpoilerWithMaximumLengthTitleTest()
        {
            // Boundary value analysis: the title field accepts at most 70 characters.
            NavigateTo(TestSettings.BaseUrl + "/Story/Add");

            var maxTitleLength = 70;
            var boundaryTitle = TitlePrefix + GenerateRandomString(maxTitleLength - TitlePrefix.Length);

            var titleInput = driver.FindElement(By.Id("title"));
            Assert.That(titleInput.GetAttribute("maxlength"), Is.EqualTo(maxTitleLength.ToString()),
                "The title field should declare a 70 character limit");

            titleInput.SendKeys(boundaryTitle);
            driver.FindElement(By.Id("description")).SendKeys(createdDescription);
            driver.FindElement(By.CssSelector("button[type='submit']")).Click();

            wait.Until(d => !d.Url.Contains("/Story/Add"));

            var titles = GetAllTitles();
            Assert.Multiple(() =>
            {
                Assert.That(titles.Last(), Is.EqualTo(boundaryTitle),
                    "A title of exactly the maximum allowed length should be accepted");
                Assert.That(titles.Last(), Has.Length.EqualTo(maxTitleLength),
                    "The stored title should keep all 70 characters");
            });

            // Leave the application as we found it by removing the test spoiler again.
            NavigateTo(GetActionLink(GetSpoilers().Last(), "/Story/Delete"));

            wait.Until((_) =>
            {
                var remaining = GetAllTitles();
                return remaining.Count == titles.Count - 1 && !remaining.Contains(boundaryTitle);
            });
        }

        // -------------------------------------------------------------------
        // Authentication
        // -------------------------------------------------------------------

        [Test, Order(8)]
        public void LogOutRemovesTheSessionTest()
        {
            NavigateTo(TestSettings.BaseUrl);

            // The link text is rendered as "LOGOUT" through CSS text-transform,
            // so it is located by href rather than by its visible text.
            driver.FindElement(By.CssSelector("a[href='/User/Logout']")).Click();

            // Polled through JavaScript: a FindElement poll would block for the
            // full implicit wait on every iteration while the page navigates.
            wait.Until((_) => ((IJavaScriptExecutor)driver).ExecuteScript(
                "return document.querySelector(\"a[href='/User/Login']\") !== null;"));

            Assert.Multiple(() =>
            {
                Assert.That(driver.Url, Is.EqualTo(TestSettings.BaseUrl + "/"),
                    "Logging out should return the user to the anonymous home page");
                Assert.That(ElementExists("a[href='/Story/Add']"), Is.False,
                    "An anonymous visitor must not see the 'Create Spoiler' link");
                Assert.That(ElementExists("a[href='/User/Logout']"), Is.False,
                    "The logout link should be gone after logging out");
            });
        }

        [Test, Order(9)]
        public void LoginWithInvalidPasswordShowsErrorTest()
        {
            NavigateTo(TestSettings.BaseUrl + "/User/Login");

            driver.FindElement(By.Id("username")).SendKeys(TestSettings.Username);
            driver.FindElement(By.Id("password")).SendKeys("definitely-not-the-password");
            driver.FindElement(By.CssSelector("button[type='submit']")).Click();

            wait.Until((_) => ((IJavaScriptExecutor)driver).ExecuteScript(
                "return document.querySelector(\".validation-summary-errors li\") !== null;"));

            Assert.Multiple(() =>
            {
                Assert.That(driver.Url, Is.EqualTo(TestSettings.BaseUrl + "/User/Login"),
                    "A rejected login must keep the user on the login page");
                Assert.That(GetValidationSummary(), Does.Contain("Unable to sign in!"),
                    "The sign-in failure message should be displayed");
            });
        }

        [Test, Order(10)]
        public void RegisterWithAlreadyTakenEmailTest()
        {
            NavigateTo(TestSettings.BaseUrl + "/User/Register");

            driver.FindElement(By.Id("username")).SendKeys(TestSettings.Username);
            driver.FindElement(By.Id("email")).SendKeys(TestSettings.Email);
            driver.FindElement(By.Id("firstName")).SendKeys(TestSettings.FirstName);
            driver.FindElement(By.Id("midName")).SendKeys(TestSettings.MiddleName);
            driver.FindElement(By.Id("lastName")).SendKeys(TestSettings.LastName);
            driver.FindElement(By.Id("password")).SendKeys(TestSettings.Password);
            driver.FindElement(By.Id("rePassword")).SendKeys(TestSettings.Password);
            driver.FindElement(By.CssSelector("button[type='submit']")).Click();

            wait.Until((_) => ((IJavaScriptExecutor)driver).ExecuteScript(
                "return document.querySelector(\".validation-summary-errors li\") !== null;"));

            Assert.Multiple(() =>
            {
                Assert.That(driver.Url, Is.EqualTo(TestSettings.BaseUrl + "/User/Register"),
                    "Registering an existing e-mail must keep the user on the registration page");
                Assert.That(GetValidationSummary(), Does.Contain("Email already taken!"),
                    "The duplicate e-mail message should be displayed");
            });
        }

        // -------------------------------------------------------------------
        // Helpers
        // -------------------------------------------------------------------

        /// <summary>
        /// Asserts that requesting the given action for an unknown spoiler id makes
        /// the application report a missing spoiler. The response is a plain-text
        /// HTTP 404, so the message lives in the page source rather than in an
        /// HTML element.
        /// </summary>
        private void AssertPageReportsMissingSpoiler(string actionPath)
        {
            NavigateTo(
                TestSettings.BaseUrl + $"{actionPath}?storyId=00000000-0000-0000-0000-000000000000");

            Assert.That(driver.PageSource, Does.Contain("No such spoiler!"),
                $"Requesting {actionPath} for an unknown id should report a missing spoiler");
        }

        /// <summary>
        /// Returns only the sections that represent an actual spoiler. The home
        /// page also contains static marketing sections, which are filtered out
        /// by the presence of the per-spoiler Edit link.
        /// </summary>
        private List<IWebElement> GetSpoilers() =>
            driver.FindElements(By.CssSelector("section#scroll"))
                  .Where(s => s.FindElements(By.CssSelector("a[href*='/Story/Edit']")).Count > 0)
                  .ToList();

        private List<string> GetAllTitles() =>
            GetSpoilers().Select(GetTitle).ToList();

        private static string GetTitle(IWebElement spoiler) =>
            spoiler.FindElement(By.CssSelector("h2.display-4")).Text.Trim();

        /// <summary>
        /// Returns the absolute target of an action link (Edit / Delete) belonging
        /// to the given spoiler.
        /// </summary>
        private static string GetActionLink(IWebElement spoiler, string actionPath)
        {
            var href = spoiler
                .FindElement(By.CssSelector($"a[href*='{actionPath}']"))
                .GetAttribute("href");

            Assert.That(href, Is.Not.Null.And.Not.Empty,
                $"Every spoiler should expose a '{actionPath}' link");
            return href!;
        }

        /// <summary>
        /// Navigates to a URL and waits until the document has finished loading.
        /// The spoiler list makes the home page heavy, and interacting with it
        /// while it is still loading makes clicks land on stale coordinates.
        /// </summary>
        private void NavigateTo(string url)
        {
            driver.Navigate().GoToUrl(url);

            wait.Until((_) => ((IJavaScriptExecutor)driver).ExecuteScript(
                "return document.readyState")?.ToString() == "complete");
        }

        private string GetValidationSummary() =>
            driver.FindElement(By.CssSelector(".validation-summary-errors")).Text;

        /// <summary>
        /// Reports whether an element is present without waiting. Used for
        /// negative assertions, where the implicit wait would otherwise stall
        /// for its full timeout before concluding that nothing matched.
        /// </summary>
        private bool ElementExists(string cssSelector) =>
            ((IJavaScriptExecutor)driver).ExecuteScript(
                "return document.querySelector(arguments[0]) !== null;", cssSelector) is true;

        /// <summary>
        /// Registers the test account. Silently ignored when the account already
        /// exists, which is the normal case for a repeat run of the suite.
        /// </summary>
        private void TryRegister()
        {
            NavigateTo(TestSettings.BaseUrl + "/User/Register");

            driver.FindElement(By.Id("username")).SendKeys(TestSettings.Username);
            driver.FindElement(By.Id("email")).SendKeys(TestSettings.Email);
            driver.FindElement(By.Id("firstName")).SendKeys(TestSettings.FirstName);
            driver.FindElement(By.Id("midName")).SendKeys(TestSettings.MiddleName);
            driver.FindElement(By.Id("lastName")).SendKeys(TestSettings.LastName);
            driver.FindElement(By.Id("password")).SendKeys(TestSettings.Password);
            driver.FindElement(By.Id("rePassword")).SendKeys(TestSettings.Password);
            driver.FindElement(By.CssSelector("button[type='submit']")).Click();
        }

        private void Login()
        {
            NavigateTo(TestSettings.BaseUrl);
            driver.FindElement(By.CssSelector("a[href='/User/Login']")).Click();

            driver.FindElement(By.Id("username")).SendKeys(TestSettings.Username);
            driver.FindElement(By.Id("password")).SendKeys(TestSettings.Password);
            driver.FindElement(By.CssSelector("button[type='submit']")).Click();

            wait.Until(d => d.Url.TrimEnd('/') == TestSettings.BaseUrl.TrimEnd('/'));
        }

        private static string GenerateRandomString(int length)
        {
            const string chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
            var random = new Random();
            return new string(Enumerable.Range(0, length)
                .Select(_ => chars[random.Next(chars.Length)])
                .ToArray());
        }
    }
}