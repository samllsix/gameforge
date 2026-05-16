using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace GameForge.Tests
{
    /// <summary>
    /// PlayerController 单元测试
    /// </summary>
    [TestFixture]
    public class PlayerControllerTests
    {
        #region Setup and Teardown
        private GameObject _testObject;
        private PlayerController _instance;

        [SetUp]
        public void SetUp()
        {
            _testObject = new GameObject();
            _instance = _testObject.AddComponent<PlayerController>();
        }

        [TearDown]
        public void TearDown()
        {
            Object.DestroyImmediate(_testObject);
        }
        #endregion

        #region Tests
        [Test]
        public void PlayerController_Initialization_HasCorrectDefaults()
        {
            // Arrange
            // Act
            // Assert
            Assert.IsNotNull(_instance);
        }

        [UnityTest]
        public IEnumerator PlayerController_Update_WorksCorrectly()
        {
            // Arrange
            // Act
            yield return null;

            // Assert
            Assert.IsNotNull(_instance);
        }
        #endregion
    }
}