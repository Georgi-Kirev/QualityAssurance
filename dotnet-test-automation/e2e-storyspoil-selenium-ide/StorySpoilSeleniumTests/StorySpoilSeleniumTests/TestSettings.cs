using System;
using OpenQA.Selenium;
using OpenQA.Selenium.Chrome;

namespace StorySpoilSeleniumTests
{
    /// <summary>
    /// Central configuration for the Story Spoil end-to-end suite.
    ///
    /// Every value can be overridden through environment variables, which keeps
    /// environment-specific data (URLs, accounts) out of the source code:
    ///
    ///   STORYSPOIL_BASEURL
    ///   STORYSPOIL_USERNAME
    ///   STORYSPOIL_EMAIL
    ///   STORYSPOIL_FIRSTNAME
    ///   STORYSPOIL_MIDDLENAME
    ///   STORYSPOIL_LASTNAME
    ///   STORYSPOIL_PASSWORD
    ///   STORYSPOIL_HEADLESS   (true / false)
    /// </summary>
    public static class TestSettings
    {
        public static string BaseUrl { get; } =
            Read("STORYSPOIL_BASEURL", "http://144.91.123.158:100").TrimEnd('/');

        public static string Username { get; } = Read("STORYSPOIL_USERNAME", "Gogo");
        public static string Email { get; } = Read("STORYSPOIL_EMAIL", "Gogo123@abv.eu");
        public static string FirstName { get; } = Read("STORYSPOIL_FIRSTNAME", "Georgi");
        public static string MiddleName { get; } = Read("STORYSPOIL_MIDDLENAME", "Naimov");
        public static string LastName { get; } = Read("STORYSPOIL_LASTNAME", "Petkov");
        public static string Password { get; } = Read("STORYSPOIL_PASSWORD", "Gnp123456");

        /// <summary>
        /// Runs the browser without a visible window by default. Set
        /// STORYSPOIL_HEADLESS=false to watch the tests run.
        /// </summary>
        public static bool Headless { get; } =
            !string.Equals(Read("STORYSPOIL_HEADLESS", "true"), "false",
                StringComparison.OrdinalIgnoreCase);

        public static IWebDriver CreateDriver()
        {
            var options = new ChromeOptions();

            if (Headless)
            {
                options.AddArgument("--headless=new");
                options.AddArgument("--window-size=1920,1080");
            }

            // Chrome's password manager and leak detection steal focus and
            // interfere with typing into the forms.
            options.AddUserProfilePreference("credentials_enable_service", false);
            options.AddUserProfilePreference("profile.password_manager_enabled", false);
            options.AddUserProfilePreference("profile.password_manager_leak_detection", false);
            options.AddUserProfilePreference("autofill.profile_enabled", false);
            options.AddArgument("--disable-notifications");
            options.AddArgument("--disable-save-password-bubble");
            options.AddArgument(
                "--disable-features=PasswordLeakDetection,PasswordManagerOnboarding,AutofillServerCommunication");

            var driver = new ChromeDriver(options);
            driver.Manage().Timeouts().ImplicitWait = TimeSpan.FromSeconds(10);
            return driver;
        }

        private static string Read(string variable, string fallback)
        {
            var value = Environment.GetEnvironmentVariable(variable);
            return string.IsNullOrWhiteSpace(value) ? fallback : value;
        }
    }
}