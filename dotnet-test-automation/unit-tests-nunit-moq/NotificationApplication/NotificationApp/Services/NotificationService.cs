using System;
using NotificationApp.Interfaces;
using NotificationApp.Entities;

namespace NotificationApp.Services
{
    public class NotificationService
    {
        private readonly IUserRepository _userRepository;
        private readonly INotifier _notifier;

        public NotificationService(IUserRepository userRepository, INotifier notifier)
        {
            _userRepository = userRepository;
            _notifier = notifier;
        }

        public void NotifyUser(int userId, string message)
        {
            if (string.IsNullOrWhiteSpace(message))
            {
                throw new ArgumentException("Message cannot be empty.");
            }

            var user = _userRepository.GetUserById(userId);
            if (user == null)
            {
                throw new ArgumentException("User not found.");
            }

            if (!user.IsActive)
            {
                throw new InvalidOperationException("Cannot notify inactive user.");
            }

            _notifier.Send(user.Email, message);
        }
    }
}
