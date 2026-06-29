import unittest
import sys
import os
import threading
import time
from unittest.mock import patch

# Adjust path to import from workspace Cwd
sys.path.append(os.getcwd())

from src.services.resilience import CircuitBreaker, CircuitBreakerOpenException, resilient_call, resilient_call_with_fallback
from src.services.providers import get_vanna

class TestResilienceCircuit(unittest.TestCase):
    
    # ----------------------------------------------------------------
    # 1. Circuit Breaker State Transitions
    # ----------------------------------------------------------------
    def test_circuit_breaker_transitions(self):
        breaker = CircuitBreaker("TestBreaker", failure_threshold=2, recovery_timeout=0.5)
        self.assertEqual(breaker.state, "CLOSED")
        
        # First failure
        breaker.record_failure()
        self.assertEqual(breaker.state, "CLOSED")
        self.assertEqual(breaker.failure_count, 1)
        
        # Second failure (trips the breaker)
        breaker.record_failure()
        self.assertEqual(breaker.state, "OPEN")
        self.assertEqual(breaker.failure_count, 2)
        
        # Verify fail-fast raises exception immediately
        with self.assertRaises(CircuitBreakerOpenException):
            breaker.check_state()
            
        # Wait for recovery timeout
        time.sleep(0.6)
        
        # Check state: should transition to HALF_OPEN
        breaker.check_state()
        self.assertEqual(breaker.state, "HALF_OPEN")
        
        # Successful call resets state to CLOSED
        breaker.record_success()
        self.assertEqual(breaker.state, "CLOSED")
        self.assertEqual(breaker.failure_count, 0)

    # ----------------------------------------------------------------
    # 2. Resilient Call Retries & Fallbacks
    # ----------------------------------------------------------------
    def test_resilient_call_retry_success(self):
        breaker = CircuitBreaker("TestBreakerRetry", failure_threshold=3, recovery_timeout=10.0)
        attempts = []
        
        def mock_func():
            attempts.append(1)
            if len(attempts) < 2:
                raise ValueError("Transient error")
            return "Success"
            
        result = resilient_call(breaker, mock_func)
        self.assertEqual(result, "Success")
        self.assertEqual(len(attempts), 2)  # Succeeded on 2nd attempt (1 retry)
        self.assertEqual(breaker.state, "CLOSED")

    def test_resilient_call_fail_and_trip(self):
        breaker = CircuitBreaker("TestBreakerFail", failure_threshold=2, recovery_timeout=10.0)
        
        def mock_failing_func():
            raise RuntimeError("Persistent crash")
            
        # Should exhaust all 3 retries, then record 1 failure on the breaker
        with self.assertRaises(RuntimeError):
            resilient_call(breaker, mock_failing_func)
            
        self.assertEqual(breaker.state, "CLOSED")  # Threshold is 2, only 1 failure recorded so far
        
        # Second call fails and trips breaker
        with self.assertRaises(RuntimeError):
            resilient_call(breaker, mock_failing_func)
            
        self.assertEqual(breaker.state, "OPEN")

    def test_fallback_handling(self):
        breaker = CircuitBreaker("TestBreakerFallback", failure_threshold=1, recovery_timeout=10.0)
        breaker.record_failure()  # Trips breaker immediately (threshold 1)
        
        def dummy():
            return "OK"
            
        result = resilient_call_with_fallback(breaker, "FALLBACK_VAL", dummy)
        self.assertEqual(result, "FALLBACK_VAL")

    # ----------------------------------------------------------------
    # 3. Thread Safety & Eval Mode Logic
    # ----------------------------------------------------------------
    def test_circuit_breaker_thread_safety(self):
        breaker = CircuitBreaker("ConcurrencyTestBreaker", failure_threshold=5, recovery_timeout=1.0)
        
        # Spawn 20 threads to record failures concurrently
        threads = []
        for _ in range(20):
            t = threading.Thread(target=breaker.record_failure)
            threads.append(t)
            t.start()
            
        for t in threads:
            t.join()
            
        # Verify that state transitioned to OPEN and failure count is accurate
        self.assertEqual(breaker.state, "OPEN")
        self.assertEqual(breaker.failure_count, 20)

    def test_retry_deamplification_under_eval_mode(self):
        breaker = CircuitBreaker("EvalModeRetryTest", failure_threshold=5)
        
        attempts = 0
        def failing_func():
            nonlocal attempts
            attempts += 1
            raise RuntimeError("Outage")
            
        # Case A: Eval mode active -> should fail after exactly 1 attempt (no retries)
        os.environ["LOCAL_RAG_EVAL_MODE"] = "1"
        with self.assertRaises(RuntimeError):
            resilient_call(breaker, failing_func)
        self.assertEqual(attempts, 1)
        
        # Case B: Eval mode inactive -> should retry 3 times
        os.environ.pop("LOCAL_RAG_EVAL_MODE", None)
        attempts = 0
        with self.assertRaises(RuntimeError):
            resilient_call(breaker, failing_func)
        self.assertEqual(attempts, 3)

    # ----------------------------------------------------------------
    # 4. Provider Caching
    # ----------------------------------------------------------------
    def test_get_vanna_cache_poisoning_and_reconnection(self):
        import src.services.providers as providers
        providers._cached_vanna = None
        
        postgres_online = False
        call_count = 0
        
        def mock_connect_postgres(**kwargs):
            nonlocal call_count
            call_count += 1
            if not postgres_online:
                raise ConnectionError("Postgres offline")
            return True
            
        with patch("src.services.providers.MyVanna.connect_to_postgres", side_effect=mock_connect_postgres):
            # Attempt 1: Postgres is offline
            vn1 = get_vanna(connect_postgres=True)
            self.assertGreaterEqual(call_count, 1)
            self.assertIsNone(providers._cached_vanna)
            
            # Attempt 2: Postgres is back online
            postgres_online = True
            vn2 = get_vanna(connect_postgres=True)
            
            # Since Postgres succeeded, it should cache the Vanna instance
            self.assertIsNotNone(providers._cached_vanna)
            
            # Save the count of calls before cached retrieval
            current_calls = call_count
            
            # Attempt 3: Retrieve cached Vanna instance (should not trigger new connection attempt)
            vn3 = get_vanna(connect_postgres=True)
            self.assertEqual(call_count, current_calls)
            self.assertIs(vn2, vn3)

if __name__ == "__main__":
    unittest.main()
