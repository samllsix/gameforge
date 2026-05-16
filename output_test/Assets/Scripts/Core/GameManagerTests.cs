using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace GameForge.Tests
{
    /// <summary>
    /// GameManager 单元测试
    /// </summary>
    [TestFixture]
    public class GameManagerTests
    {
        #region Setup and Teardown
        private GameObject _testObject;
        private GameManager _instance;

        [SetUp]
        public void SetUp()
        {
            _testObject = new GameObject();
            _instance = _testObject.AddComponent<GameManager>();
        }

        [TearDown]
        public void TearDown()
        {
            Object.DestroyImmediate(_testObject);
        }
        #endregion

        #region Tests
        [Test]
        public void GameManager_Initialization_HasCorrectDefaults()
        {
            // Arrange
            // Act
            // Assert
            Assert.IsNotNull(_instance);
        }

        [UnityTest]
        public IEnumerator GameManager_Update_WorksCorrectly()
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