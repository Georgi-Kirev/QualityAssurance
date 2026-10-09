using Moq;
using NotificationApp.Entities;
using NotificationApp.Interfaces;
using NotificationApp.Services;
using System;

namespace NotificationApp.Tests
{
    /// <summary>
    /// Unit tests for <see cref="NotificationService"/>.
    ///
    /// Both collaborators are mocked, which lets the tests drive the service
    /// through every branch - happy path, inactive user, unknown user and
    /// invalid input - and then assert that the notifier was (or was not) used.
    /// </summary>
    [TestFixture]
    public class NotificationAppTests
    {
        private Mock<IUserRepository> _mockUserRepo;
        private Mock<INotifier> _mockNotifier;
        private NotificationService _notificationService;

        [SetUp]
        public void Setup()
        {
            // Arrange: Create mock objects for the dependencies
            _mockUserRepo = new Mock<IUserRepository>();
            _mockNotifier = new Mock<INotifier>();

            // Inject mocks into the NotificationService
            _notificationService = new NotificationService(_mockUserRepo.Object, _mockNotifier.Object);
        }

        [Test]
        public void NotifyUser_WithValidActiveUser_CallsSend()
        {
            // Arrange: Mock an active user with valid email
            var activeUser = new User { Id = 1, Email = "active@test.com", IsActive = true };
            _mockUserRepo.Setup(r => r.GetUserById(1)).Returns(activeUser);

            // Act: Call the method
            _notificationService.NotifyUser(1, "Hello");

            // Assert: Verify that Send was called once with correct parameters
            _mockNotifier.Verify(n => n.Send("active@test.com", "Hello"), Times.Once);
        }

        [Test]
        public void NotifyUser_WithInactiveUser_ThrowsInvalidOperationException()
        {
            // Arrange: Mock an inactive user
            var inactiveUser = new User { Id = 1, Email = "inactive@test.com", IsActive = false };
            _mockUserRepo.Setup(r => r.GetUserById(1)).Returns(inactiveUser);

            // Act & Assert: Expect an exception, and Send should not be called
            Assert.Throws<InvalidOperationException>(() => _notificationService.NotifyUser(1, "Hello"));
            _mockNotifier.Verify(n => n.Send(It.IsAny<string>(), It.IsAny<string>()), Times.Never);
        }

        [Test]
        public void NotifyUser_WithNonExistentUser_ThrowsArgumentException()
        {
            // Arrange: Mock a non-existent user
            _mockUserRepo.Setup(r => r.GetUserById(1)).Returns((User)null!);

            // Act & Assert: Expect an exception, and Send should not be called
            Assert.Throws<ArgumentException>(() => _notificationService.NotifyUser(1, "Hello"));
            _mockNotifier.Verify(n => n.Send(It.IsAny<string>(), It.IsAny<string>()), Times.Never);
        }

        [TestCase(null)]
        [TestCase("")]
        [TestCase("   ")]
        public void NotifyUser_WithEmptyMessage_ThrowsArgumentException(string? message)
        {
            // Arrange: the user exists, so the failure can only come from the message
            var activeUser = new User { Id = 1, Email = "active@test.com", IsActive = true };
            _mockUserRepo.Setup(r => r.GetUserById(1)).Returns(activeUser);

            // Act & Assert
            var exception = Assert.Throws<ArgumentException>(
                () => _notificationService.NotifyUser(1, message!));

            Assert.That(exception!.Message, Is.EqualTo("Message cannot be empty."));
            _mockNotifier.Verify(n => n.Send(It.IsAny<string>(), It.IsAny<string>()), Times.Never);
        }

        [Test]
        public void NotifyUser_WithEmptyMessage_ShouldNotEvenQueryTheRepository()
        {
            // Act & Assert: input validation happens before any repository call
            Assert.Throws<ArgumentException>(
                () => _notificationService.NotifyUser(1, string.Empty));

            _mockUserRepo.Verify(r => r.GetUserById(It.IsAny<int>()), Times.Never);
        }

        [Test]
        public void NotifyUser_WithNonExistentUser_ShouldReportTheExpectedMessage()
        {
            // Arrange
            _mockUserRepo.Setup(r => r.GetUserById(42)).Returns((User)null!);

            // Act
            var exception = Assert.Throws<ArgumentException>(
                () => _notificationService.NotifyUser(42, "Hello"));

            // Assert
            Assert.That(exception!.Message, Is.EqualTo("User not found."));
        }

        [Test]
        public void NotifyUser_WithInactiveUser_ShouldReportTheExpectedMessage()
        {
            // Arrange
            _mockUserRepo.Setup(r => r.GetUserById(2))
                .Returns(new User { Id = 2, Email = "sleepy@test.com", IsActive = false });

            // Act
            var exception = Assert.Throws<InvalidOperationException>(
                () => _notificationService.NotifyUser(2, "Hello"));

            // Assert
            Assert.That(exception!.Message, Is.EqualTo("Cannot notify inactive user."));
        }

        [Test]
        public void NotifyUser_ShouldForwardTheExactMessageToTheNotifier()
        {
            // Arrange
            const string message = "Your order has shipped.";
            _mockUserRepo.Setup(r => r.GetUserById(10))
                .Returns(new User { Id = 10, Email = "buyer@test.com", IsActive = true });

            // Act
            _notificationService.NotifyUser(10, message);

            // Assert
            _mockNotifier.Verify(n => n.Send("buyer@test.com", message), Times.Once);
            _mockNotifier.VerifyNoOtherCalls();
        }

        [Test]
        public void NotifyUser_CalledTwice_ShouldNotifyOncePerCall()
        {
            // Arrange
            _mockUserRepo.Setup(r => r.GetUserById(3))
                .Returns(new User { Id = 3, Email = "repeat@test.com", IsActive = true });

            // Act
            _notificationService.NotifyUser(3, "First");
            _notificationService.NotifyUser(3, "Second");

            // Assert
            _mockNotifier.Verify(n => n.Send("repeat@test.com", "First"), Times.Once);
            _mockNotifier.Verify(n => n.Send("repeat@test.com", "Second"), Times.Once);
        }
    }
}