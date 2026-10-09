namespace NotificationApp.Entities
{
    public class User
    {
        public int Id { get; set; }
        public string Email { get; set; } = string.Empty;
        public bool IsActive { get; set; }
    }
}
