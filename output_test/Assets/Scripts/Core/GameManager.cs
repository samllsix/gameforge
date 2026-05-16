using UnityEngine;
using UnityEngine.SceneManagement;

namespace GameForge.Core
{
    /// <summary>
    /// 游戏管理器 - 管理游戏状态和流程
    /// </summary>
    public class GameManager : MonoBehaviour
    {
        #region Singleton
        public static GameManager Instance { get; private set; }
        #endregion

        #region Fields
        [SerializeField] private int _maxLives = 3;

        private int _currentLives;
        private int _score;
        private bool _isGameOver;
        #endregion

        #region Properties
        public int CurrentLives => _currentLives;
        public int Score => _score;
        public bool IsGameOver => _isGameOver;
        #endregion

        #region Unity Lifecycle
        private void Awake()
        {
            if (Instance == null)
            {
                Instance = this;
                DontDestroyOnLoad(gameObject);
                InitializeGame();
            }
            else
            {
                Destroy(gameObject);
            }
        }
        #endregion

        #region Public Methods
        /// <summary>
        /// 初始化游戏
        /// </summary>
        public void InitializeGame()
        {
            _currentLives = _maxLives;
            _score = 0;
            _isGameOver = false;
        }

        /// <summary>
        /// 增加分数
        /// </summary>
        public void AddScore(int points)
        {
            _score += points;
        }

        /// <summary>
        /// 减少生命
        /// </summary>
        public void LoseLife()
        {
            _currentLives--;

            if (_currentLives <= 0)
            {
                GameOver();
            }
        }

        /// <summary>
        /// 游戏结束
        /// </summary>
        public void GameOver()
        {
            _isGameOver = true;
            Debug.Log("Game Over!");
        }

        /// <summary>
        /// 重新开始游戏
        /// </summary>
        public void RestartGame()
        {
            InitializeGame();
            SceneManager.LoadScene(SceneManager.GetActiveScene().buildIndex);
        }
        #endregion
    }
}