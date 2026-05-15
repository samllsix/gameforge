"""GameForge - 代码生成Agent模块

负责根据任务描述生成游戏代码。
"""

from typing import Any, Dict, List
from src.agents.base import BaseAgent
from src.core.state.game_state import GameDevState, AgentType, TaskType


class CodeGeneratorAgent(BaseAgent):
    """代码生成Agent

    负责：
    - 根据任务描述生成代码
    - 支持Unity C#和Unreal C++
    - 遵循项目编码规范
    """

    def __init__(self, config: Dict[str, Any]):
        """初始化代码生成Agent

        Args:
            config: 配置信息
        """
        super().__init__(AgentType.CODE_GENERATOR, config)
        self.supported_engines = self.agent_config.get("supported_engines", ["unity", "unreal"])

    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        """执行代码生成任务

        Args:
            state: 当前游戏开发状态

        Returns:
            包含生成代码的状态更新
        """
        self.log_action("code_generator_execute")

        # 获取任务信息
        task = kwargs.get("task")
        if not task:
            self.log_error("no_task_provided")
            return {"error_log": ["No task provided"]}

        # 生成代码
        code_artifacts = await self.generate(state, task)

        return {
            "code_generated": {
                **state.get("code_generated", {}),
                **{art["file_path"]: art["content"] for art in code_artifacts}
            },
            "code_artifacts": state.get("code_artifacts", []) + code_artifacts,
            "current_phase": "code_generated",
        }

    async def generate(self, state: GameDevState, task: Dict[str, Any]) -> List[Dict[str, Any]]:
        """生成代码

        Args:
            state: 当前游戏开发状态
            task: 任务信息

        Returns:
            代码产物列表
        """
        self.log_action("generate_code", {"task_id": task.get("id")})

        # 确定游戏引擎
        engine = state.get("project_context", {}).get("engine", "unity")

        # 根据任务类型生成代码
        task_type = task.get("type", TaskType.CODE.value)
        if task_type == TaskType.CODE.value:
            return await self._generate_game_code(task, engine)
        else:
            self.log_error("unsupported_task_type", {"type": task_type})
            return []

    async def _generate_game_code(self, task: Dict[str, Any], engine: str) -> List[Dict[str, Any]]:
        """生成游戏代码

        Args:
            task: 任务信息
            engine: 游戏引擎

        Returns:
            代码产物列表
        """
        task_name = task.get("name", "")
        task_description = task.get("description", "")

        # TODO: 实现基于LLM的代码生成
        # 这里先返回示例代码
        if "Player" in task_name:
            return self._generate_player_code(engine)
        elif "GameManager" in task_name:
            return self._generate_game_manager_code(engine)
        elif "碰撞" in task_name or "Collision" in task_name:
            return self._generate_collision_code(engine)
        elif "计分" in task_name or "Score" in task_name:
            return self._generate_score_code(engine)
        else:
            return self._generate_generic_code(task_name, engine)

    def _generate_player_code(self, engine: str) -> List[Dict[str, Any]]:
        """生成Player控制器代码

        Args:
            engine: 游戏引擎

        Returns:
            代码产物列表
        """
        if engine == "unity":
            return [{
                "file_path": "Assets/Scripts/Player/PlayerController.cs",
                "content": '''using UnityEngine;

namespace GameForge.Player
{
    /// <summary>
    /// 玩家控制器 - 处理移动和跳跃
    /// </summary>
    public class PlayerController : MonoBehaviour
    {
        #region Fields
        [SerializeField] private float _moveSpeed = 5f;
        [SerializeField] private float _jumpForce = 10f;
        [SerializeField] private LayerMask _groundLayer;

        private Rigidbody2D _rb;
        private bool _isGrounded;
        private float _moveInput;
        #endregion

        #region Unity Lifecycle
        private void Awake()
        {
            _rb = GetComponent<Rigidbody2D>();
            if (_rb == null)
            {
                Debug.LogError("Rigidbody2D component not found!");
                enabled = false;
                return;
            }
        }

        private void Update()
        {
            _moveInput = Input.GetAxisRaw("Horizontal");

            if (Input.GetButtonDown("Jump") && _isGrounded)
            {
                Jump();
            }
        }

        private void FixedUpdate()
        {
            Move();
        }
        #endregion

        #region Public Methods
        /// <summary>
        /// 移动玩家
        /// </summary>
        public void Move()
        {
            _rb.velocity = new Vector2(_moveInput * _moveSpeed, _rb.velocity.y);
        }

        /// <summary>
        /// 跳跃
        /// </summary>
        public void Jump()
        {
            _rb.velocity = new Vector2(_rb.velocity.x, _jumpForce);
            _isGrounded = false;
        }
        #endregion

        #region Collision Detection
        private void OnCollisionEnter2D(Collision2D collision)
        {
            if (((1 << collision.gameObject.layer) & _groundLayer) != 0)
            {
                _isGrounded = true;
            }
        }
        #endregion
    }
}''',
                "language": "csharp",
                "engine": "unity",
            }]
        else:
            return [{
                "file_path": "Source/GameForge/Player/PlayerCharacter.cpp",
                "content": '''#include "PlayerCharacter.h"
#include "GameFramework/CharacterMovementComponent.h"

APlayerCharacter::APlayerCharacter()
{
    PrimaryActorTick.bCanEverTick = true;

    // 设置移动参数
    GetCharacterMovement()->MaxWalkSpeed = 600.0f;
    GetCharacterMovement()->JumpZVelocity = 1000.0f;
}

void APlayerCharacter::BeginPlay()
{
    Super::BeginPlay();
}

void APlayerCharacter::Tick(float DeltaTime)
{
    Super::Tick(DeltaTime);
}

void APlayerCharacter::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
    Super::SetupPlayerInputComponent(PlayerInputComponent);

    PlayerInputComponent->BindAxis("MoveForward", this, &APlayerCharacter::MoveForward);
    PlayerInputComponent->BindAxis("MoveRight", this, &APlayerCharacter::MoveRight);
    PlayerInputComponent->BindAction("Jump", IE_Pressed, this, &ACharacter::Jump);
}

void APlayerCharacter::MoveForward(float Value)
{
    if (Value != 0.0f)
    {
        AddMovementInput(GetActorForwardVector(), Value);
    }
}

void APlayerCharacter::MoveRight(float Value)
{
    if (Value != 0.0f)
    {
        AddMovementInput(GetActorRightVector(), Value);
    }
}''',
                "language": "cpp",
                "engine": "unreal",
            }]

    def _generate_game_manager_code(self, engine: str) -> List[Dict[str, Any]]:
        """生成GameManager代码"""
        if engine == "unity":
            return [{
                "file_path": "Assets/Scripts/Core/GameManager.cs",
                "content": '''using UnityEngine;
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
}''',
                "language": "csharp",
                "engine": "unity",
            }]
        else:
            return []

    def _generate_collision_code(self, engine: str) -> List[Dict[str, Any]]:
        """生成碰撞检测代码"""
        return []

    def _generate_score_code(self, engine: str) -> List[Dict[str, Any]]:
        """生成计分系统代码"""
        return []

    def _generate_generic_code(self, task_name: str, engine: str) -> List[Dict[str, Any]]:
        """生成通用代码"""
        return []
