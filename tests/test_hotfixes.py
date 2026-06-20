import unittest
import sys
import os
import threading
import time

# Adjust path to import from workspace Cwd
sys.path.append(os.getcwd())

from src.services.resilience import CircuitBreaker, CircuitBreakerOpenException, resilient_call
from src.services.providers import get_vanna, MyVanna, _cached_vanna

class TestHotfixes(unittest.TestCase):
    
    def setUp(self):
        # Reset cached vanna instance between tests
        import src.services.providers as providers
        providers._cached_vanna = None

    def test_get_vanna_cache_poisoning_and_reconnection(self):
        postgres_online = False
        call_count = 0
        
        def mock_connect_postgres(**kwargs):
            nonlocal call_count
            call_count += 1
            if not postgres_online:
                raise ConnectionError("Postgres offline")
            return True
            
        with unittest.mock.patch("src.services.providers.MyVanna.connect_to_postgres", side_effect=mock_connect_postgres):
            # Attempt 1: Postgres is offline
            vn1 = get_vanna(connect_postgres=True)
            self.assertGreaterEqual(call_count, 1)
            
            # Since Postgres failed, it should NOT cache the Vanna instance
            import src.services.providers as providers
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
            self.assertEqual(call_count, current_calls)  # Connection count should remain unchanged
            self.assertIs(vn2, vn3)

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

if __name__ == "__main__":
    unittest.main()
