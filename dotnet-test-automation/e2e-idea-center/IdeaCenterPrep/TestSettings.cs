using System;
using OpenQA.Selenium;
using OpenQA.Selenium.Chrome;

namespace IdeaCenterPrep
{
    /// <summary>
    /// Central configuration for the Idea Center end-to-end suite.
    ///
    /// Every value can be overridden through environment variables so that
    /// environment-specific data stays out of the source code:
    ///
    ///   IDEACENTER_BASEURL
    ///   IDEACENTER_EMAIL
    ///   IDEACENTER_PASSWORD
    ///   IDEACENTER_HEADLESS   (true / false)
    /// </summary>
    public static class TestSettings
    {
        public static string BaseUrl { get; } =
            Read("IDEACENTER_BASEURL", "http://144.91.123.158:82").TrimEnd('/');

        public static string Email { get; } = Read("IDEACENTER_EMAIL", "ivo1234@gmail.com");
        public static string Password { get; } = Read("IDEACENTER_PASSWORD", "ivo1234");

        /// <summary>
        /// Runs the browser without a visible window by default. Set
        /// IDEACENTER_HEADLESS=false to watch the tests execute.
        /// </summary>
        public static bool Headless { get; } =
            !string.Equals(Read("IDEACENTER_HEADLESS", "true"), "false",
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