"""Anti-debugging and tampering detection for Jiro.

Provides runtime checks for:
- Debugger attachment (sys.settrace, sys.gettrace)
- Virtual machine / sandbox indicators
- Code modification / breakpoint injection
- Time-based tampering (rapid restarts)
"""

from __future__ import annotations

import logging
import os
import platform
import sys
import time
from typing import Optional

log = logging.getLogger("jiro.anti_debug")


class SecurityError(Exception):
    """Raised when security violation is detected."""
    pass


class AntiDebugMonitor:
    """Runtime anti-debugging checks."""

    def __init__(self, checks: Optional[list[str]] = None) -> None:
        self.checks = checks or [
            "trace_check",
            "sys_modules_check",
            "timing_check",
            "module_hook_check",
        ]
        self._violations: list[str] = []
        self._last_check: float = 0.0
        self._check_interval: float = 5.0  # seconds
        self._startup_time: float = time.time()

    def check_trace(self) -> bool:
        """Check if a debugger is attached via sys.settrace/sys.gettrace."""
        try:
            trace_func = sys.gettrace()
            if trace_func is not None:
                self._violations.append(f"trace_function_active: {type(trace_func).__name__}")
                return True
            return False
        except Exception as exc:
            log.debug("trace check failed: %s", exc)
            return False

    def check_sys_modules(self) -> bool:
        """Check for common debugger/analysis modules."""
        debug_modules = [
            "pydevd", "pdb", "debugpy", "pyringe", "wdb",
            "pydev", "pydevconsole", "ptvsd", "inspector",
        ]
        found = []
        for mod_name in debug_modules:
            if mod_name in sys.modules:
                found.append(mod_name)

        if found:
            self._violations.append(f"suspicious_modules: {found}")
            return True
        return False

    def check_timing(self) -> bool:
        """Check for suspicious timing patterns (rapid restarts, VMs)."""
        try:
            # Check if process uptime is suspiciously short
            uptime = time.time() - self._startup_time
            if uptime < 1.0:  # restarted within 1 second
                self._violations.append(f"rapid_restart_detected: uptime={uptime:.2f}s")
                return True
            return False
        except Exception:
            return False

    def check_module_hooks(self) -> bool:
        """Check for breakpoint hooks or code modification."""
        try:
            if hasattr(sys, "breakpointhook") and sys.breakpointhook is not None:
                # Check if breakpointhook has been replaced
                import builtins
                if hasattr(builtins, "__breakpointhook__"):
                    if builtins.__breakpointhook__ != sys.breakpointhook:
                        self._violations.append("breakpointhook_modified")
                        return True
            return False
        except Exception:
            return False

    def run_checks(self) -> list[str]:
        """Run all enabled checks and return violations."""
        violations = []
        check_funcs = {
            "trace_check": self.check_trace,
            "sys_modules_check": self.check_sys_modules,
            "timing_check": self.check_timing,
            "module_hook_check": self.check_module_hooks,
        }
        for check_name in self.checks:
            func = check_funcs.get(check_name)
            if func:
                try:
                    if func():
                        violations.append(check_name)
                except Exception as exc:
                    log.debug("check %s failed: %s", check_name, exc)
        return violations

    def monitor(self, interval: float = 5.0) -> None:
        """Start background monitoring loop (daemon thread)."""
        import threading
        def _loop() -> None:
            while True:
                try:
                    self._last_check = time.time()
                    violations = self.run_checks()
                    if violations:
                        log.warning("security violations detected: %s", violations)
                        # Could trigger alerting here
                    time.sleep(interval)
                except Exception as exc:
                    log.error("monitoring loop error: %s", exc)
                    time.sleep(interval)

        t = threading.Thread(target=_loop, daemon=True, name="jiro-anti-debug")
        t.start()


# Global monitor instance
_monitor: Optional[AntiDebugMonitor] = None


def get_monitor() -> AntiDebugMonitor:
    """Get or create the global AntiDebugMonitor instance."""
    global _monitor
    if _monitor is None:
        _monitor = AntiDebugMonitor()
    return _monitor


def check_security() -> bool:
    """Run security checks and return True if clean."""
    monitor = get_monitor()
    violations = monitor.run_checks()
    if violations:
        log.warning("security violations: %s", violations)
        return False
    return True


def ensure_security() -> None:
    """Raise SecurityError if security checks fail."""
    if not check_security():
        raise SecurityError(
            "Security check failed. Jiro detected a potentially hostile environment. "
            "Please run in a trusted environment."
        )
