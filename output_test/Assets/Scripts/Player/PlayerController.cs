using UnityEngine;

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
}