namespace NotificationApp.Interfaces
{
    public interface INotifier
    {
        void Send(string email, string message);
    }
}
