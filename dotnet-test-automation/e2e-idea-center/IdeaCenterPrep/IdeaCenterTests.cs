using OpenQA.Selenium;
using OpenQA.Selenium.Interactions;
using OpenQA.Selenium.Support.UI;

namespace IdeaCenterPrep
{
    /// <summary>
    /// End-to-end regression suite for the "Idea Center" web application.
    ///
    /// Target application and test account are configured through
    /// <see cref="TestSettings"/> (environment variables), so no credentials
    /// are hard-coded in the repository.
    ///
    /// Tests 1-6 form one ordered CRUD scenario: an idea is created, viewed,
    /// edited and finally deleted again, so each step builds on the data created
    /// by the previous one. Tests 7-10 cover navigation, authentication and the
    /// application's response to an unknown idea id.
    /// </summary>
    [TestFixture]
    [NonParallelizable]
    public class IdeaCenterTests
    {
        private IWebDriver driver = null!;
        private WebDriverWait wait = null!;

        private const string TitleFieldId = "form3Example1c";
        private const string DescriptionFieldId = "form3Example4cd";
        private const string IdeaCardSelector = ".card.mb-4.box-shadow";

        private static string lastCreatedIdeaTitle = string.Empty;
        private static string lastCreatedIdeaDescription = string.Empty;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            driver = TestSettings.CreateDriver();
            wait = new WebDriverWait(driver, TimeSpan.FromSeconds(20));

            driver.Manage().Timeouts().ImplicitWait = TimeSpan.FromSeconds(10);

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
        // Ordered CRUD scenario
        // -------------------------------------------------------------------

        [Test, Order(1)]
        public void CreateIdeaWithInvalidDataTest()
        {
            NavigateTo(TestSettings.BaseUrl + "/Ideas/Create");
            AcceptAlertIfPresent();

            driver.FindElement(By.Id(TitleFieldId)).SendKeys("");
            driver.FindElement(By.Id(DescriptionFieldId)).SendKeys("");
            driver.FindElement(By.CssSelector("button[type='submit']")).Click();

            wait.Until(d => d.Url.Contains("/Ideas/Create"));

            Assert.Multiple(() =>
            {
                Assert.That(driver.Url, Is.EqualTo(TestSettings.BaseUrl + "/Ideas/Create"),
                    "The page should remain on the Idea Creation page");
                Assert.That(
                    driver.FindElement(By.CssSelector(".validation-summary-errors li")).Text,
                    Is.EqualTo("Unable to create new Idea!"),
                    "The main error message should be displayed");
            });
        }

        [Test, Order(2)]
        public void CreateRandomIdeaTest()
        {
            lastCreatedIdeaTitle = "Idea" + GenerateRandomString(8);
            lastCreatedIdeaDescription = "Description" + GenerateRandomString(8);

            NavigateTo(TestSettings.BaseUrl + "/Ideas/Create");
            AcceptAlertIfPresent();

            driver.FindElement(By.Id(TitleFieldId)).Clear();
            driver.FindElement(By.Id(TitleFieldId)).SendKeys(lastCreatedIdeaTitle);
            driver.FindElement(By.Id(DescriptionFieldId)).Clear();
            driver.FindElement(By.Id(DescriptionFieldId)).SendKeys(lastCreatedIdeaDescription);
            driver.FindElement(By.CssSelector("button[type='submit']")).Click();
            AcceptAlertIfPresent();

            wait.Until(d => d.Url.EndsWith("/Ideas/MyIdeas"));

            var lastIdeaCard = GetLastIdeaCard();
            Assert.Multiple(() =>
            {
                Assert.That(driver.Url, Is.EqualTo(TestSettings.BaseUrl + "/Ideas/MyIdeas"),
                    "The page should redirect to MyIdeas after successful creation");
                Assert.That(
                    lastIdeaCard.FindElement(By.CssSelector(".card-text")).Text.Trim(),
                    Is.EqualTo(lastCreatedIdeaDescription),
                    "The description of the last created idea should match the expected description");
            });
        }

        [Test, Order(3)]
        public void ViewLastCreatedIdeaTest()
        {
            NavigateTo(TestSettings.BaseUrl + "/Ideas/MyIdeas");

            var lastIdeaCard = GetLastIdeaCard();
            ClickCardAction(lastIdeaCard, "a[href*='/Ideas/Read']");

            wait.Until(d => d.Url.Contains("/Ideas/Read"));

            Assert.Multiple(() =>
            {
                Assert.That(driver.FindElement(By.CssSelector("h1.mb-0")).Text.Trim(),
                    Is.EqualTo(lastCreatedIdeaTitle),
                    "The idea title on the details page should match the last created idea title");
                Assert.That(driver.FindElement(By.CssSelector("h1.mb-0")).Text.Trim(),
                    Does.Contain(lastCreatedIdeaTitle.Substring(0, 10)),
                    "The details page should show the newest idea first");
            });
        }

        [Test, Order(4)]
        public void EditLastCreatedIdeaTitleTest()
        {
            NavigateTo(TestSettings.BaseUrl + "/Ideas/MyIdeas");

            var lastIdeaCard = GetLastIdeaCard();
            ClickCardAction(lastIdeaCard, "a[href*='/Ideas/Edit']");

            wait.Until(d => d.Url.Contains("/Ideas/Edit"));

            lastCreatedIdeaTitle = "Changed Title: " + lastCreatedIdeaTitle;

            var titleField = driver.FindElement(By.Id(TitleFieldId));
            titleField.Clear();
            titleField.SendKeys(lastCreatedIdeaTitle);
            driver.FindElement(By.CssSelector("button[type='submit']")).Click();
            AcceptAlertIfPresent();

            NavigateTo(TestSettings.BaseUrl + "/Ideas/MyIdeas");
            ClickCardAction(GetLastIdeaCard(), "a[href*='/Ideas/Read']");

            wait.Until(d => d.Url.Contains("/Ideas/Read"));

            Assert.That(driver.FindElement(By.CssSelector("h1.mb-0")).Text.Trim(),
                Is.EqualTo(lastCreatedIdeaTitle),
                "The idea title should match the edited value");
        }

        [Test, Order(5)]
        public void EditIdeaDescriptionTest()
        {
            NavigateTo(TestSettings.BaseUrl + "/Ideas/MyIdeas");

            var lastIdeaCard = GetLastIdeaCard();
            ClickCardAction(lastIdeaCard, "a[href*='/Ideas/Edit']");

            wait.Until(d => d.Url.Contains("/Ideas/Edit"));

            lastCreatedIdeaDescription = "Changed Description: " + lastCreatedIdeaDescription;

            var descriptionField = driver.FindElement(By.Id(DescriptionFieldId));
            descriptionField.Clear();
            descriptionField.SendKeys(lastCreatedIdeaDescription);
            driver.FindElement(By.CssSelector("button[type='submit']")).Click();
            AcceptAlertIfPresent();

            NavigateTo(TestSettings.BaseUrl + "/Ideas/MyIdeas");

            Assert.That(
                GetLastIdeaCard().FindElement(By.CssSelector(".card-text")).Text.Trim(),
                Is.EqualTo(lastCreatedIdeaDescription),
                "The idea description should match the edited value");
        }

        [Test, Order(6)]
        public void DeleteLastIdeaTest()
        {
            NavigateTo(TestSettings.BaseUrl + "/Ideas/MyIdeas");

            var countBefore = GetIdeaCards().Count;
            Assert.That(countBefore, Is.GreaterThan(0), "There should be at least one idea card");

            ClickCardAction(GetLastIdeaCard(), "a[href*='/Ideas/Delete']");
            AcceptAlertIfPresent();

            NavigateTo(TestSettings.BaseUrl + "/Ideas/MyIdeas");

            var descriptions = GetIdeaCards()
                .Select(card => card.FindElement(By.CssSelector(".card-text")).Text.Trim())
                .ToList();

            Assert.Multiple(() =>
            {
                Assert.That(descriptions, Has.Count.EqualTo(countBefore - 1),
                    "Deleting an idea should remove exactly one card from the list");
                Assert.That(descriptions, Does.Not.Contain(lastCreatedIdeaDescription),
                    "The deleted idea description should no longer be present on My Ideas");
            });
        }

        // -------------------------------------------------------------------
        // Navigation
        // -------------------------------------------------------------------

        [Test, Order(7)]
        public void MyIdeasPageShowsTheSignedInUserNavigationTest()
        {
            NavigateTo(TestSettings.BaseUrl + "/Ideas/MyIdeas");

            Assert.Multiple(() =>
            {
                Assert.That(ElementExists("a[href='/Profile']"), Is.True,
                    "A signed-in user should see a link to the profile page");
                Assert.That(ElementExists("a[href='/Ideas/Create']"), Is.True,
                    "A signed-in user should see a link to the idea creation page");
                Assert.That(ElementExists("a[href='/Users/Logout']"), Is.True,
                    "A signed-in user should see a way to log out");
            });
        }

        // -------------------------------------------------------------------
        // Error handling
        // -------------------------------------------------------------------

        [Test, Order(8)]
        public void EditUnknownIdeaDoesNotOpenTheEditFormTest()
        {
            NavigateTo(TestSettings.BaseUrl + "/Ideas/Edit?ideaId=00000000-0000-0000-0000-000000000000");

            Assert.Multiple(() =>
            {
                Assert.That(ElementExists("#" + TitleFieldId), Is.False,
                    "The edit form must not be rendered for an idea that does not exist");
                Assert.That(ElementExists(IdeaCardSelector), Is.True,
                    "The application should fall back to the idea list instead of failing");
            });
        }

        [Test, Order(9)]
        public void DeleteUnknownIdeaDoesNotDeleteAnyIdeaTest()
        {
            NavigateTo(TestSettings.BaseUrl + "/Ideas/MyIdeas");
            var countBefore = GetIdeaCards().Count;

            NavigateTo(TestSettings.BaseUrl + "/Ideas/Delete?ideaId=00000000-0000-0000-0000-000000000000");

            NavigateTo(TestSettings.BaseUrl + "/Ideas/MyIdeas");

            Assert.That(GetIdeaCards(), Has.Count.EqualTo(countBefore),
                "Requesting to delete an unknown idea must not remove any existing idea");
        }

        // -------------------------------------------------------------------
        // Authentication
        // -------------------------------------------------------------------

        [Test, Order(10)]
        public void LogOutRemovesTheSessionTest()
        {
            NavigateTo(TestSettings.BaseUrl + "/Ideas/MyIdeas");

            ClickLogout();

            wait.Until((_) => ((IJavaScriptExecutor)driver).ExecuteScript(
                "return document.querySelector(\"a[href='/Users/Login']\") !== null;"));

            Assert.Multiple(() =>
            {
                Assert.That(ElementExists("a[href='/Users/Login']"), Is.True,
                    "The sign-in link should be visible after logging out");
                Assert.That(ElementExists("a[href='/Ideas/Create']"), Is.False,
                    "An anonymous visitor must not see the 'Create Idea' link");
            });
        }

        [Test, Order(11)]
        public void MyIdeasRedirectsAnonymousVisitorToSignInTest()
        {
            NavigateTo(TestSettings.BaseUrl + "/Ideas/MyIdeas");

            Assert.Multiple(() =>
            {
                Assert.That(driver.Url, Does.StartWith(TestSettings.BaseUrl + "/Users/Login"),
                    "An anonymous visitor should be redirected to the sign-in page");
                Assert.That(driver.Url, Does.Contain("ReturnUrl"),
                    "The redirect should preserve the originally requested page");
            });
        }

        [Test, Order(12)]
        public void SignInWithInvalidPasswordShowsErrorTest()
        {
            NavigateTo(TestSettings.BaseUrl + "/Users/Login");

            driver.FindElement(By.Id("typeEmailX-2")).SendKeys(TestSettings.Email);
            driver.FindElement(By.Id("typePasswordX-2")).SendKeys("definitely-not-the-password");
            driver.FindElement(By.CssSelector(".btn.btn-primary.btn-lg.btn-block")).Click();

            wait.Until((_) => ((IJavaScriptExecutor)driver).ExecuteScript(
                "return document.querySelector(\".validation-summary-errors li\") !== null;"));

            Assert.Multiple(() =>
            {
                Assert.That(driver.Url, Is.EqualTo(TestSettings.BaseUrl + "/Users/Login"),
                    "A rejected sign-in must keep the user on the sign-in page");
                Assert.That(driver.FindElement(By.CssSelector(".validation-summary-errors li")).Text,
                    Is.EqualTo("Unable to sign in!"),
                    "The sign-in failure message should be displayed");
            });
        }

        // -------------------------------------------------------------------
        // Helpers
        // -------------------------------------------------------------------

        private List<IWebElement> GetIdeaCards() =>
            driver.FindElements(By.CssSelector(IdeaCardSelector)).ToList();

        private IWebElement GetLastIdeaCard()
        {
            var ideaCards = GetIdeaCards();
            Assert.That(ideaCards.Count, Is.GreaterThan(0), "There should be at least one idea card");
            return ideaCards.Last();
        }

        /// <summary>
        /// Clicks an action link inside an idea card, scrolling it into view
        /// first so that the click is dispatched at the right coordinates.
        /// </summary>
        private void ClickCardAction(IWebElement card, string linkSelector)
        {
            var link = card.FindElement(By.CssSelector(linkSelector));
            new Actions(driver).MoveToElement(link).Click().Perform();
        }

        private void ClickLogout()
        {
            driver.FindElement(By.CssSelector("a[href='/Users/Logout']")).Click();
        }

        private void Login()
        {
            driver.Navigate().GoToUrl(TestSettings.BaseUrl + "/Users/Login");

            driver.FindElement(By.Id("typeEmailX-2")).SendKeys(TestSettings.Email);
            driver.FindElement(By.Id("typePasswordX-2")).SendKeys(TestSettings.Password);
            driver.FindElement(By.CssSelector(".btn.btn-primary.btn-lg.btn-block")).Click();

            wait.Until(d => !d.Url.Contains("/Users/Login"));
        }

        /// <summary>
        /// Navigates to a URL and waits until the document has finished loading,
        /// so that later clicks are not dispatched at stale coordinates.
        /// </summary>
        private void NavigateTo(string url)
        {
            driver.Navigate().GoToUrl(url);

            wait.Until((_) => ((IJavaScriptExecutor)driver).ExecuteScript(
                "return document.readyState")?.ToString() == "complete");
        }

        /// <summary>
        /// Reports whether an element is present without waiting. Used for
        /// negative assertions, where the implicit wait would otherwise stall
        /// for its full timeout before concluding that nothing matched.
        /// </summary>
        private bool ElementExists(string cssSelector) =>
            ((IJavaScriptExecutor)driver).ExecuteScript(
                "return document.querySelector(arguments[0]) !== null;", cssSelector) is true;

        private void AcceptAlertIfPresent()
        {
            // A short, dedicated timeout: most of the time no alert is shown at
            // all, and the suite should not pay for a full wait in that case.
            var alertWait = new WebDriverWait(driver, TimeSpan.FromSeconds(2));

            try
            {
                alertWait.Until(drv =>
                {
                    try
                    {
                        drv.SwitchTo().Alert();
                        return true;
                    }
                    catch (NoAlertPresentException)
                    {
                        return false;
                    }
                });

                driver.SwitchTo().Alert().Accept();
            }
            catch (WebDriverTimeoutException)
            {
                // No alert was shown - nothing to accept.
            }
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