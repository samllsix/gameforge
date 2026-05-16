"""GameForge - 代码生成Agent模块

负责根据任务描述生成游戏代码。
"""

import re
from typing import Any, Dict, List
from src.agents.base import BaseAgent
from src.core.state.game_state import GameDevState, AgentType, TaskType
from src.utils.llm_client import get_llm_client


class CodeGeneratorAgent(BaseAgent):
    """代码生成Agent

    负责：
    - 根据任务描述生成代码
    - 支持Unity C#和Unreal C++
    - 遵循项目编码规范
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(AgentType.CODE_GENERATOR, config)
        self.llm = get_llm_client(config)
        self.supported_engines = self.agent_config.get("supported_engines", ["unity", "unreal"])

    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        self.log_action("code_generator_execute")

        task = kwargs.get("task")
        if not task:
            self.log_error("no_task_provided")
            return {"error_log": ["No task provided"]}

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
        self.log_action("generate_code", {"task_id": task.get("id")})

        engine = state.get("project_context", {}).get("engine", "unity")
        project_name = state.get("project_context", {}).get("project_name", "GameForge")

        task_type = task.get("type", TaskType.CODE.value)
        if task_type != TaskType.CODE.value:
            self.log_error("unsupported_task_type", {"type": task_type})
            return []

        return await self._generate_game_code(task, engine, project_name, state)

    async def _generate_game_code(
        self, task: Dict[str, Any], engine: str, project_name: str, state: GameDevState
    ) -> List[Dict[str, Any]]:
        system_prompt = self.get_prompt_template("code_generator_system")

        # 收集已生成的代码作为上下文
        existing_code = state.get("code_generated", {})
        existing_context = ""
        if existing_code:
            existing_context = "\n\n## 已生成的代码文件\n"
            for path, content in existing_code.items():
                existing_context += f"\n### {path}\n```\n{content[:500]}\n...\n```\n"

        user_prompt = f"""请根据以下任务生成完整的代码实现。

项目名称: {project_name}
游戏引擎: {engine}
任务ID: {task.get('id', 'unknown')}
任务名称: {task.get('name', '')}
任务描述: {task.get('description', '')}
{existing_context}

要求：
1. 生成完整可编译的代码，不要省略任何部分
2. 遵循系统提示中的命名规范和代码结构
3. 使用 namespace {project_name.replace(' ', '.')}.[ModuleName] 格式
4. 包含必要的注释
5. 代码必须是可以直接复制到Unity项目中使用的

请直接输出代码，格式如下：
```csharp
// 文件: Assets/Scripts/[Module]/[FileName].cs
namespace ...
{
    ...
}
```

如果需要生成多个文件，每个文件用单独的代码块。"""

        try:
            response = self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.llm_config.get("temperature", 0.3),
                max_tokens=self.llm_config.get("max_tokens", 8192),
            )

            artifacts = self._parse_code_response(response, task, engine)

            if not artifacts:
                self.log_error("no_code_parsed", {"response_preview": response[:200]})
                return self._fallback_generate(task, engine)

            self.log_action("code_generated", {"file_count": len(artifacts)})
            return artifacts

        except Exception as e:
            self.log_error("code_generator_llm_error", {"error": str(e)})
            return self._fallback_generate(task, engine)

    def _parse_code_response(
        self, response: str, task: Dict[str, Any], engine: str
    ) -> List[Dict[str, Any]]:
        """解析LLM返回的代码响应

        Args:
            response: LLM原始响应
            task: 任务信息
            engine: 游戏引擎

        Returns:
            代码产物列表
        """
        artifacts = []

        # 匹配所有代码块
        code_blocks = re.findall(
            r'```(?:csharp|cs|cpp)?\s*\n(.*?)\n```',
            response,
            re.DOTALL,
        )

        for block in code_blocks:
            block = block.strip()
            if not block:
                continue

            # 尝试从注释中提取文件路径
            file_path_match = re.search(r'//\s*文件:\s*(\S+)', block)
            if file_path_match:
                file_path = file_path_match.group(1)
                # 移除文件路径注释
                block = re.sub(r'//\s*文件:\s*\S+\s*\n', '', block, count=1).strip()
            else:
                # 根据任务名称推断文件路径
                file_path = self._infer_file_path(task, engine, len(artifacts))

            artifacts.append({
                "file_path": file_path,
                "content": block,
                "language": "csharp" if engine == "unity" else "cpp",
                "engine": engine,
            })

        # 如果没有匹配到代码块，尝试把整个响应当作代码
        if not artifacts and response.strip() and not response.strip().startswith('{'):
            # 检查是否像C#代码
            if 'class ' in response or 'namespace ' in response or 'using ' in response:
                file_path = self._infer_file_path(task, engine, 0)
                artifacts.append({
                    "file_path": file_path,
                    "content": response.strip(),
                    "language": "csharp" if engine == "unity" else "cpp",
                    "engine": engine,
                })

        return artifacts

    def _infer_file_path(self, task: Dict[str, Any], engine: str, index: int) -> str:
        """根据任务信息推断文件路径"""
        task_name = task.get("name", "")

        if engine == "unity":
            if "Player" in task_name or "玩家" in task_name:
                return "Assets/Scripts/Player/PlayerController.cs"
            elif "GameManager" in task_name or "游戏管理" in task_name:
                return "Assets/Scripts/Core/GameManager.cs"
            elif "碰撞" in task_name or "Collision" in task_name:
                return "Assets/Scripts/Core/CollisionHandler.cs"
            elif "计分" in task_name or "Score" in task_name:
                return "Assets/Scripts/Core/ScoreManager.cs"
            elif "测试" in task_name or "Test" in task_name:
                return f"Assets/Scripts/Tests/Test_{index}.cs"
            else:
                return f"Assets/Scripts/Generated/Generated_{index}.cs"
        else:
            return f"Source/GameForge/Generated/Generated_{index}.cpp"

    def _fallback_generate(self, task: Dict[str, Any], engine: str) -> List[Dict[str, Any]]:
        """LLM调用失败时的回退生成"""
        task_name = task.get("name", "")

        if engine == "unity":
            if "Player" in task_name or "玩家" in task_name:
                return self._generate_player_code(engine)
            elif "GameManager" in task_name or "游戏管理" in task_name:
                return self._generate_game_manager_code(engine)

        return [{
            "file_path": self._infer_file_path(task, engine, 0),
            "content": f"// TODO: Implement {task_name}\n// Auto-generated placeholder\nusing UnityEngine;\n\nnamespace GameForge.Generated\n{{\n    public class {task_name.replace(' ', '')} : MonoBehaviour\n    {{\n        private void Awake() {{ }}\n        private void Update() {{ }}\n    }}\n}}",
            "language": "csharp" if engine == "unity" else "cpp",
            "engine": engine,
        }]

    def _generate_player_code(self, engine: str) -> List[Dict[str, Any]]:
        if engine == "unreal":
            return [{
                "file_path": "Source/GameForge/Player/PlayerCharacter.cpp",
                "content": '''#include "PlayerCharacter.h"
#include "GameFramework/CharacterMovementComponent.h"

APlayerCharacter::APlayerCharacter()
{
    PrimaryActorTick.bCanEverTick = true;
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
        [SerializeField] private float _groundCheckDistance = 0.15f;

        private Rigidbody2D _rb;
        private BoxCollider2D _col;
        private float _moveInput;
        private bool _jumpRequested;
        private float _jumpCooldown;
        #endregion

        #region Unity Lifecycle
        private void Awake()
        {
            _rb = GetComponent<Rigidbody2D>();
            _col = GetComponent<BoxCollider2D>();
            if (_rb == null || _col == null)
            {
                Debug.LogError("Required component missing!");
                enabled = false;
                return;
            }
        }

        private void Update()
        {
            _moveInput = Input.GetAxisRaw("Horizontal");
            if (Input.GetButtonDown("Jump"))
                _jumpRequested = true;
        }

        private void FixedUpdate()
        {
            Move();
            _jumpCooldown -= Time.fixedDeltaTime;
            if (_jumpRequested && IsGrounded() && _jumpCooldown <= 0f)
            {
                Jump();
                _jumpRequested = false;
                _jumpCooldown = 0.25f;
            }
            if (!IsGrounded())
                _jumpRequested = false;
        }
        #endregion

        #region Ground Detection
        public bool IsGrounded()
        {
            Vector2 origin = (Vector2)transform.position + _col.offset;
            Vector2 size = _col.size * 0.85f;
            int groundMask = LayerMask.GetMask("Ground");
            var hit = Physics2D.BoxCast(origin, size, 0f, Vector2.down,
                _groundCheckDistance, groundMask);
            return hit.collider != null;
        }
        #endregion

        #region Public Methods
        public void Move()
        {
            _rb.velocity = new Vector2(_moveInput * _moveSpeed, _rb.velocity.y);
        }

        public void Jump()
        {
            _rb.velocity = new Vector2(_rb.velocity.x, _jumpForce);
        }
        #endregion
    }
}''',
                "language": "csharp",
                "engine": "unity",
            }]
        return []

    def _generate_game_manager_code(self, engine: str) -> List[Dict[str, Any]]:
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
        public void InitializeGame()
        {
            _currentLives = _maxLives;
            _score = 0;
            _isGameOver = false;
        }

        public void AddScore(int points)
        {
            _score += points;
        }

        public void LoseLife()
        {
            _currentLives--;
            if (_currentLives <= 0)
            {
                GameOver();
            }
        }

        public void GameOver()
        {
            _isGameOver = true;
            Debug.Log("Game Over!");
        }

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
        return []

    def _generate_collision_code(self, engine: str) -> List[Dict[str, Any]]:
        if engine == "unity":
            return [{
                "file_path": "Assets/Scripts/Core/CollisionHandler.cs",
                "content": '''using UnityEngine;

namespace GameForge.Core
{
    /// <summary>
    /// 碰撞处理器 - 统一管理碰撞检测逻辑
    /// </summary>
    public class CollisionHandler : MonoBehaviour
    {
        #region Events
        public static event System.Action<Collision2D> OnCollision;
        public static event System.Action<Collider2D> OnTrigger;
        #endregion

        #region Fields
        [SerializeField] private string[] _collisionTags = { "Player", "Enemy", "Pickup" };
        [SerializeField] private bool _debugCollisions = false;
        #endregion

        #region Collision Callbacks
        private void OnCollisionEnter2D(Collision2D collision)
        {
            if (!ShouldProcessCollision(collision.gameObject)) return;

            if (_debugCollisions)
                Debug.Log($"Collision with {collision.gameObject.name}");

            OnCollision?.Invoke(collision);
            ProcessCollision(collision);
        }

        private void OnTriggerEnter2D(Collider2D other)
        {
            if (!ShouldProcessCollision(other.gameObject)) return;

            if (_debugCollisions)
                Debug.Log($"Trigger with {other.gameObject.name}");

            OnTrigger?.Invoke(other);
            ProcessTrigger(other);
        }
        #endregion

        #region Processing
        private bool ShouldProcessCollision(GameObject other)
        {
            foreach (var tag in _collisionTags)
            {
                if (other.CompareTag(tag)) return true;
            }
            return false;
        }

        private void ProcessCollision(Collision2D collision)
        {
            var scoreManager = FindObjectOfType<ScoreManager>();
            if (scoreManager == null) return;

            if (collision.gameObject.CompareTag("Enemy"))
                scoreManager.OnPlayerHit();
            else if (collision.gameObject.CompareTag("Pickup"))
                scoreManager.OnPickupCollected(collision.gameObject);
        }

        private void ProcessTrigger(Collider2D other)
        {
            if (other.CompareTag("Pickup"))
            {
                var scoreManager = FindObjectOfType<ScoreManager>();
                scoreManager?.OnPickupCollected(other.gameObject);
            }
        }
        #endregion
    }
}''',
                "language": "csharp",
                "engine": engine,
            }]
        return []

    def _generate_score_code(self, engine: str) -> List[Dict[str, Any]]:
        if engine == "unity":
            return [{
                "file_path": "Assets/Scripts/Core/ScoreManager.cs",
                "content": '''using UnityEngine;
using UnityEngine.Events;

namespace GameForge.Core
{
    /// <summary>
    /// 计分管理器 - 管理游戏分数和连击系统
    /// </summary>
    public class ScoreManager : MonoBehaviour
    {
        #region Events
        [System.Serializable]
        public class ScoreEvent : UnityEvent<int> { }

        public ScoreEvent OnScoreChanged = new ScoreEvent();
        public UnityEvent OnComboMaxed = new UnityEvent();
        #endregion

        #region Fields
        [Header("Score Settings")]
        [SerializeField] private int _baseScore = 100;
        [SerializeField] private int _pickupScore = 50;
        [SerializeField] private int _enemyScore = 200;

        [Header("Combo Settings")]
        [SerializeField] private float _comboWindow = 2.0f;
        [SerializeField] private int _maxComboMultiplier = 8;
        [SerializeField] private int _comboStep = 1;

        private int _currentScore;
        private int _currentCombo;
        private float _comboTimer;
        private int _highScore;
        #endregion

        #region Properties
        public int CurrentScore => _currentScore;
        public int HighScore => _highScore;
        public int CurrentCombo => _currentCombo;
        public int ComboMultiplier => Mathf.Min(1 + _currentCombo * _comboStep, _maxComboMultiplier);
        #endregion

        #region Unity Lifecycle
        private void Awake()
        {
            _highScore = PlayerPrefs.GetInt("HighScore", 0);
        }

        private void Update()
        {
            if (_currentCombo > 0)
            {
                _comboTimer -= Time.deltaTime;
                if (_comboTimer <= 0f)
                    ResetCombo();
            }
        }
        #endregion

        #region Public Methods
        public void AddScore(int points)
        {
            int multiplied = points * ComboMultiplier;
            _currentScore += multiplied;
            OnScoreChanged?.Invoke(_currentScore);

            if (_currentScore > _highScore)
            {
                _highScore = _currentScore;
                PlayerPrefs.SetInt("HighScore", _highScore);
            }
        }

        public void OnEnemyDefeated()
        {
            AddScore(_enemyScore);
            IncrementCombo();
        }

        public void OnPlayerHit()
        {
            ResetCombo();
        }

        public void OnPickupCollected(GameObject pickup)
        {
            AddScore(_pickupScore);
            Destroy(pickup);
        }

        public void ResetScore()
        {
            _currentScore = 0;
            ResetCombo();
            OnScoreChanged?.Invoke(_currentScore);
        }
        #endregion

        #region Combo System
        private void IncrementCombo()
        {
            _currentCombo++;
            _comboTimer = _comboWindow;

            if (_currentCombo >= _maxComboMultiplier)
                OnComboMaxed?.Invoke();
        }

        private void ResetCombo()
        {
            _currentCombo = 0;
            _comboTimer = 0f;
        }
        #endregion
    }
}''',
                "language": "csharp",
                "engine": engine,
            }]
        return []

    def _generate_generic_code(self, task_name: str, engine: str) -> List[Dict[str, Any]]:
        if engine == "unity":
            class_name = task_name.replace(" ", "").replace("-", "").replace("_", "")
            if not class_name or not class_name[0].isalpha():
                class_name = "Generated" + class_name

            return [{
                "file_path": f"Assets/Scripts/Generated/{class_name}.cs",
                "content": f'''using UnityEngine;

namespace GameForge.Generated
{{
    /// <summary>
    /// {task_name} - 自动生成的组件
    /// </summary>
    public class {class_name} : MonoBehaviour
    {{
        #region Fields
        [SerializeField] private bool _enabled = true;
        [SerializeField] private float _updateInterval = 0.1f;
        private float _timer;
        #endregion

        #region Unity Lifecycle
        private void Awake()
        {{
            Initialize();
        }}

        private void Update()
        {{
            if (!_enabled) return;

            _timer += Time.deltaTime;
            if (_timer >= _updateInterval)
            {{
                _timer = 0f;
                OnTick();
            }}
        }}
        #endregion

        #region Protected Methods
        protected virtual void Initialize()
        {{
            // 初始化逻辑
        }}

        protected virtual void OnTick()
        {{
            // 定时更新逻辑
        }}
        #endregion

        #region Public Methods
        public void SetEnabled(bool enabled)
        {{
            _enabled = enabled;
        }}
        #endregion
    }}
}}''',
                "language": "csharp",
                "engine": engine,
            }]
        return []
