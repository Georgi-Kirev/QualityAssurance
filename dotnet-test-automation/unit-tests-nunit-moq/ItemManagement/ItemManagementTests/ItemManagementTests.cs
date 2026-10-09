using Moq;
using ItemManagementApp.Services;
using ItemManagementLib.Models;
using ItemManagementLib.Repositories;
using System;
using System.Collections.Generic;
using System.Linq;

namespace ItemManagement.Tests
{
    /// <summary>
    /// Unit tests for <see cref="ItemService"/>.
    ///
    /// The repository dependency is replaced with a Moq mock, so the service can
    /// be exercised in isolation: no database and no Entity Framework context is
    /// involved. Every test follows Arrange - Act - Assert and verifies both the
    /// value returned by the service and the interaction with the repository.
    /// </summary>
    [TestFixture]
    public class ItemServiceTests
    {
        private Mock<IItemRepository> _mockRepository;
        private ItemService _itemService;

        [SetUp]
        public void Setup()
        {
            // Arrange: Create a mock instance of IItemRepository
            _mockRepository = new Mock<IItemRepository>();

            // Instantiate ItemService with the mocked repository
            _itemService = new ItemService(_mockRepository.Object);
        }

        [Test]
        public void AddItem_ShouldCallAddItemOnRepository()
        {
            // Arrange: Tell the mock what to do when AddItem is called on it
            // (nothing needs to be returned here, because the method returns void)

            // Act: Call AddItem on the service
            _itemService.AddItem("Laptop");

            // Assert: Verify that AddItem was called on the repository
            _mockRepository.Verify(r => r.AddItem(It.Is<Item>(item => item.Name == "Laptop")), Times.Once);
        }

        [Test]
        public void GetAllItems_ShouldReturnAllItems()
        {
            // Arrange: Setup mock repository to return a list of items
            var items = new List<Item>
            {
                new Item { Id = 1, Name = "Laptop" },
                new Item { Id = 2, Name = "Mouse" },
                new Item { Id = 3, Name = "Monitor" }
            };
            _mockRepository.Setup(r => r.GetAllItems()).Returns(items);

            // Act: Call GetAllItems on the service
            var result = _itemService.GetAllItems();

            // Assert: Check that the result matches the mock data
            Assert.That(result, Is.EqualTo(items));
            _mockRepository.Verify(r => r.GetAllItems(), Times.Once);
        }

        [Test]
        public void GetAllItems_WhenRepositoryIsEmpty_ShouldReturnEmptyCollection()
        {
            // Arrange
            _mockRepository.Setup(r => r.GetAllItems()).Returns(new List<Item>());

            // Act
            var result = _itemService.GetAllItems();

            // Assert
            Assert.That(result, Is.Empty);
        }

        [Test]
        public void UpdateItem_ShouldCallUpdateItemOnRepository()
        {
            // Arrange: the repository returns a known item
            var existingItem = new Item { Id = 5, Name = "OldName" };
            _mockRepository.Setup(r => r.GetItemById(5)).Returns(existingItem);

            // Act
            _itemService.UpdateItem(5, "NewName");

            // Assert: the very same instance is passed back with the new name
            _mockRepository.Verify(
                r => r.UpdateItem(It.Is<Item>(item => item.Id == 5 && item.Name == "NewName")),
                Times.Once);
        }

        [Test]
        public void UpdateItem_WhenItemDoesNotExist_ShouldNotCallUpdateItemOnRepository()
        {
            // Arrange
            _mockRepository.Setup(r => r.GetItemById(99)).Returns((Item)null!);

            // Act
            _itemService.UpdateItem(99, "NewName");

            // Assert: nothing to update, so the repository must not be called
            _mockRepository.Verify(
                r => r.UpdateItem(It.IsAny<Item>()), Times.Never);
        }

        [Test]
        public void GetItemById_ShouldReturnTheItemFromTheRepository()
        {
            // Arrange
            var item = new Item { Id = 3, Name = "Monitor" };
            _mockRepository.Setup(r => r.GetItemById(3)).Returns(item);

            // Act
            var result = _itemService.GetItemById(3);

            // Assert
            Assert.That(result, Is.SameAs(item));
            _mockRepository.Verify(r => r.GetItemById(3), Times.Once);
        }

        [Test]
        public void GetItemById_WhenItemDoesNotExist_ShouldReturnNull()
        {
            // Arrange
            _mockRepository.Setup(r => r.GetItemById(404)).Returns((Item)null!);

            // Act
            var result = _itemService.GetItemById(404);

            // Assert
            Assert.That(result, Is.Null);
        }

        [Test]
        public void DeleteItem_ShouldCallDeleteItemOnRepository()
        {
            // Act
            _itemService.DeleteItem(7);

            // Assert
            _mockRepository.Verify(r => r.DeleteItem(7), Times.Once);
        }

        [Test]
        public void AddItem_WhenRepositoryRejectsTheName_ShouldPropagateTheException()
        {
            // Arrange: the repository validates the name and throws for long input
            _mockRepository
                .Setup(r => r.AddItem(It.IsAny<Item>()))
                .Throws<ArgumentException>();

            // Act & Assert
            Assert.Throws<ArgumentException>(() => _itemService.AddItem(new string('x', 50)));
        }

        [TestCase("Laptop", ExpectedResult = true)]
        [TestCase("ab", ExpectedResult = true)]
        [TestCase("0123456789", ExpectedResult = true)]
        [TestCase("01234567890", ExpectedResult = false)]
        [TestCase("", ExpectedResult = false)]
        [TestCase(null, ExpectedResult = false)]
        public bool ValidateItemName_ShouldAcceptNamesUpToTenCharacters(string? name)
        {
            return _itemService.ValidateItemName(name!);
        }

        [Test]
        public void AddItem_ShouldNotTouchTheRepositoryTwice()
        {
            // Act
            _itemService.AddItem("Keyboard");

            // Assert: exactly one interaction with the repository
            _mockRepository.Verify(r => r.AddItem(It.IsAny<Item>()), Times.Once);
            _mockRepository.VerifyNoOtherCalls();
        }
    }
}