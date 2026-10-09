using System;
using NotificationApp.Entities;
using NotificationApp.Interfaces;
using NotificationApp.Services;

namespace NotificationApp
{
    class Program
    {
        static void Main(string[] args)
        {
            // These would normally be injected via DI in a real app.
            IUserRepository userRepository = new DummyUserRepository();
            INotifier notifier = new ConsoleNotifier();

            var notificationService = new NotificationService(userRepository, notifier);

            Console.WriteLine("Enter user ID:");
            int userId = int.Parse(Console.ReadLine() ?? string.Empty);

            Console.WriteLine("Enter message to send:");
            string message = Console.ReadLine() ?? string.Empty;

            try
            {
                notificationService.NotifyUser(userId, message);
                Console.WriteLine("Notification sent.");
            }
            catch (Exception ex)
            {
                Console.WriteLine($"Error: {ex.Message}");
            }
        }
    }

    // Dummy implementations for demo purposes
    public class DummyUserRepository : IUserRepository
    {
        public User GetUserById(int id)
        {
            return new User
            {
                Id = id,
                Email = "user" + id + "@example.com",
                IsActive = (id % 2 == 0) // active if ID is even
            };
        }
    }

    public class ConsoleNotifier : INotifier
    {
        public void Send(string email, string message)
        {
            Console.WriteLine($"Sending message to {email}: {message}");
        }
    }
}
