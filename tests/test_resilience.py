import unittest
import sys
import os
import time

# Adjust path to import from workspace Cwd
sys.path.append(os.getcwd())

from src.services.resilience import CircuitBreaker, CircuitBreakerOpenException, resilient_call, resilient_call_with_fallback

class TestServiceResilience(unittest.TestCase):
    
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

if __name__ == "__main__":
    unittest.main()
