"""测试LLM客户端重试和熔断机制"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.utils.llm_client import (
    RetryConfig,
    CircuitBreaker,
    CircuitState,
    LLMClient,
)


class TestRetryConfig:
    """重试配置测试"""

    def test_default_values(self):
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.base_delay == 1.0
        assert config.max_delay == 60.0

    def test_get_delay_increases(self):
        config = RetryConfig(base_delay=1.0, exponential_base=2.0, jitter=False)
        d0 = config.get_delay(0)
        d1 = config.get_delay(1)
        d2 = config.get_delay(2)
        assert d0 < d1 < d2

    def test_get_delay_caps_at_max(self):
        config = RetryConfig(base_delay=1.0, max_delay=5.0, exponential_base=2.0, jitter=False)
        d10 = config.get_delay(10)
        assert d10 == 5.0

    def test_get_delay_with_jitter(self):
        config = RetryConfig(base_delay=10.0, jitter=True)
        delays = [config.get_delay(0) for _ in range(100)]
        # With jitter, delays should vary
        assert len(set(delays)) > 1
        # All should be in range [5.0, 15.0] (base * (0.5 + random))
        for d in delays:
            assert 5.0 <= d <= 15.0


class TestCircuitBreaker:
    """熔断器测试"""

    @pytest.mark.asyncio
    async def test_starts_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED
        assert await cb.can_execute() is True

    @pytest.mark.asyncio
    async def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            await cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert await cb.can_execute() is False

    @pytest.mark.asyncio
    async def test_half_open_after_recovery(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        await cb.record_failure()
        await cb.record_failure()
        assert cb.state == CircuitState.OPEN

        await asyncio.sleep(0.15)
        assert await cb.can_execute() is True
        assert cb.state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_closes_from_half_open(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1, half_open_max_calls=2)
        # Force open
        await cb.record_failure()
        await cb.record_failure()
        await asyncio.sleep(0.15)
        await cb.can_execute()  # Transition to HALF_OPEN

        await cb.record_success()
        await cb.record_success()
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=5)
        await cb.record_failure()
        await cb.record_failure()
        await cb.record_success()
        assert cb.failure_count == 0

    @pytest.mark.asyncio
    async def test_half_open_limits_calls(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1, half_open_max_calls=2)
        await cb.record_failure()
        await asyncio.sleep(0.15)
        await cb.can_execute()  # HALF_OPEN

        # can_execute() grants permission; record_success() consumes a slot
        assert await cb.can_execute() is True
        await cb.record_success()
        assert await cb.can_execute() is True
        await cb.record_success()
        # After half_open_max_calls successes, state should be CLOSED
        assert cb.state == CircuitState.CLOSED


class TestLLMClientRetry:
    """LLMClient重试测试"""

    @pytest.fixture
    def client(self):
        config = {
            "llm": {
                "default_model": "test-model",
                "base_url": "http://test.local/v1",
                "max_retries": 2,
                "base_retry_delay": 0.01,
                "max_retry_delay": 0.1,
                "circuit_breaker_threshold": 3,
                "circuit_breaker_recovery": 60.0,
            }
        }
        with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
            return LLMClient(config)

    @pytest.mark.asyncio
    async def test_retry_on_failure_then_success(self, client):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "success"

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[Exception("fail once"), mock_response]
        )

        with patch.object(client, "_get_async_client", return_value=mock_client):
            result = await client.chat([{"role": "user", "content": "test"}])
            assert result == "success"
            assert mock_client.chat.completions.create.call_count == 2

    @pytest.mark.asyncio
    async def test_kimi_k26_temperature_is_normalized(self, client):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "success"

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_async_client", return_value=mock_client):
            await client.chat(
                [{"role": "user", "content": "test"}],
                model="kimi-k2.6",
                temperature=0.2,
            )

        assert mock_client.chat.completions.create.call_args.kwargs["temperature"] == 1

    @pytest.mark.asyncio
    async def test_other_kimi_models_keep_requested_temperature(self):
        config = {
            "llm": {
                "default_model": "kimi-latest",
                "base_url": "http://test.local/v1",
                "max_retries": 0,
                "base_retry_delay": 0.01,
                "max_retry_delay": 0.1,
            }
        }
        client = LLMClient(config, provider_name="kimi")
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "success"

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_async_client", return_value=mock_client):
            await client.chat([{"role": "user", "content": "test"}], temperature=0.2)

        assert mock_client.chat.completions.create.call_args.kwargs["temperature"] == 0.2

    @pytest.mark.asyncio
    async def test_nonzero_temperature_does_not_use_cache(self):
        """Only temperature=0 requests are cacheable."""
        config = {
            "llm": {
                "default_model": "test-model",
                "base_url": "http://test.local/v1",
                "max_retries": 0,
                "base_retry_delay": 0.01,
                "max_retry_delay": 0.1,
                "cache": {"enabled": True, "ttl": 3600},
            }
        }
        client = LLMClient(config)
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "success"

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_async_client", return_value=mock_client), \
             patch("src.utils.redis_client.cache_get", new_callable=AsyncMock) as cache_get:
            await client.chat([{"role": "user", "content": "test"}], temperature=0.2)

        cache_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises(self, client):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception("always fail")
        )

        with patch.object(client, "_get_async_client", return_value=mock_client):
            with pytest.raises(Exception, match="always fail"):
                await client.chat([{"role": "user", "content": "test"}])
            # max_retries=2, so total calls = 3 (initial + 2 retries)
            assert mock_client.chat.completions.create.call_count == 3

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_calls(self, client):
        # Force circuit breaker open
        for _ in range(3):
            await client.circuit_breaker.record_failure()

        with pytest.raises(RuntimeError, match="Circuit breaker OPEN"):
            await client.chat([{"role": "user", "content": "test"}])

    @pytest.mark.asyncio
    async def test_circuit_breaker_state(self, client):
        assert client.circuit_breaker.get_state() == "closed"
        for _ in range(3):
            await client.circuit_breaker.record_failure()
        assert client.circuit_breaker.get_state() == "open"
