using NotificationApp.Entities;

namespace NotificationApp.Interfaces
{
    public interface IUserRepository
    {
        User GetUserById(int id);
    }
}
