"""
Ollama Process Manager
Handles automatic startup, health monitoring, and restart of Ollama service
"""

import asyncio
import subprocess
import time
import aiohttp
from pathlib import Path
from typing import Optional
import psutil

from backend.config import settings as _settings
import logging

logger = logging.getLogger(__name__)


class OllamaManager:
    """Manages Ollama process lifecycle and health monitoring"""

    OLLAMA_EXE_PATH = str(_settings.ollama_exe_path)
    OLLAMA_URL = _settings.ollama_base_url
    HEALTH_CHECK_INTERVAL = 600  # seconds (10 minutes)
    STARTUP_TIMEOUT = 15  # seconds
    MAX_RESTART_ATTEMPTS = 3

    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._health_task: Optional[asyncio.Task] = None
        self._running = False
        self._restart_count = 0
        self._last_check_time = 0
        self._last_status = "unknown"
        logger.info(f"[OllamaManager] Initialized")
        logger.info(f"[OllamaManager] Ollama path: {self.OLLAMA_EXE_PATH}")

    async def _check_ollama_health(self) -> bool:
        """
        Check if Ollama is responding to HTTP requests
        Returns: True if healthy, False otherwise
        """
        try:
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self.OLLAMA_URL}/api/tags") as resp:
                    if resp.status == 200:
                        return True
            return False
        except Exception as e:
            return False

    def _kill_existing_ollama_servers(self):
        """Kill any existing Ollama server processes"""
        killed_any = False
        for proc in psutil.process_iter(['name', 'cmdline', 'pid']):
            try:
                # Check if this is an ollama server process (has "serve" in command line)
                if proc.info['name'] and 'ollama' in proc.info['name'].lower():
                    cmdline = proc.info.get('cmdline', [])
                    if cmdline and 'serve' in ' '.join(cmdline).lower():
                        logger.info(f"[OllamaManager] Killing existing Ollama server (PID: {proc.info['pid']})")
                        proc.kill()
                        killed_any = True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if killed_any:
            import time
            time.sleep(1)  # Wait for processes to terminate

    def _start_ollama_process(self) -> bool:
        """
        Start Ollama process
        Returns: True if started successfully, False otherwise
        """
        try:
            # Check if executable exists
            ollama_path = Path(self.OLLAMA_EXE_PATH)
            if not ollama_path.exists():
                logger.error(f"[OllamaManager] ERROR: Ollama not found at {self.OLLAMA_EXE_PATH}")
                logger.info(f"[OllamaManager] Please install Ollama or update the path")
                return False

            # Kill any existing server processes to avoid conflicts
            self._kill_existing_ollama_servers()

            logger.info(f"[OllamaManager] Starting Ollama server...")

            # Start Ollama process (hidden window)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

            self._process = subprocess.Popen(
                [str(ollama_path), "serve"],
                startupinfo=startupinfo,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW
            )

            logger.info(f"[OllamaManager] Ollama server process started (PID: {self._process.pid})")
            return True

        except Exception as e:
            logger.warning(f"[OllamaManager] Failed to start Ollama: {e}")
            return False

    async def start_and_verify_simple(self) -> bool:
        """
        Simple startup flow: Start Ollama, health check, greenlight.
        No warmup - just verify the server is responding.
        Returns: True if healthy, False otherwise
        """
        logger.info(f"[OllamaManager] Simple start: Starting Ollama server...")

        # First check if already healthy
        if await self._check_ollama_health():
            logger.info(f"[OllamaManager] Ollama already running and healthy - GREENLIGHT")
            self._last_status = "healthy"
            return True

        # Try to start process
        if not self._start_ollama_process():
            self._last_status = "failed_to_start"
            return False

        # Wait for Ollama to respond (up to 10 seconds with 1s intervals)
        logger.info(f"[OllamaManager] Waiting for Ollama health check...")
        for i in range(10):
            await asyncio.sleep(1)
            if await self._check_ollama_health():
                logger.info(f"[OllamaManager] Ollama healthy after {i+1}s - GREENLIGHT")
                self._last_status = "healthy"
                return True
            logger.info(f"[OllamaManager] Health check {i+1}/10...")

        logger.info(f"[OllamaManager] Ollama not responding after 10s")
        self._last_status = "timeout"
        return False

    async def start_and_verify(self, single_check: bool = False) -> bool:
        """
        Start Ollama and verify it's responding
        Args:
            single_check: If True, only check health once (no retry loop)
        Returns: True if started and healthy, False otherwise
        """
        logger.info(f"[OllamaManager] Starting and verifying Ollama...")

        # First check if already healthy
        if await self._check_ollama_health():
            logger.info(f"[OllamaManager] Ollama already running and healthy")
            self._last_status = "healthy"
            return True

        # Try to start process
        if not self._start_ollama_process():
            self._last_status = "failed_to_start"
            return False

        # Single check mode: wait 8 seconds then check once
        if single_check:
            logger.info(f"[OllamaManager] Waiting 8 seconds for Ollama to start...")
            await asyncio.sleep(8)

            if await self._check_ollama_health():
                logger.info(f"[OllamaManager] Ollama is healthy")
                self._last_status = "healthy"
                return True
            else:
                logger.info(f"[OllamaManager] Ollama not responding yet (will retry on next health check)")
                self._last_status = "starting"
                return False

        # Normal mode: wait up to STARTUP_TIMEOUT seconds with retries
        logger.info(f"[OllamaManager] Waiting for Ollama to become ready...")
        start_time = time.time()

        while time.time() - start_time < self.STARTUP_TIMEOUT:
            if await self._check_ollama_health():
                elapsed = time.time() - start_time
                logger.info(f"[OllamaManager] Ollama ready ({elapsed:.1f}s)")
                self._last_status = "healthy"
                return True

            await asyncio.sleep(1)

        logger.warning(f"[OllamaManager] Timeout waiting for Ollama to start")
        self._last_status = "timeout"
        return False

    async def _health_check_loop(self):
        """
        Background task that periodically checks Ollama health and restarts if needed
        """
        logger.info(f"[OllamaManager] Health check loop started (interval: {self.HEALTH_CHECK_INTERVAL}s)")

        while self._running:
            try:
                self._last_check_time = time.time()

                # Check health
                is_healthy = await self._check_ollama_health()

                if is_healthy:
                    if self._last_status != "healthy":
                        logger.info(f"[OllamaManager] Ollama is now healthy")
                    self._last_status = "healthy"
                    self._restart_count = 0  # Reset restart counter on success

                else:
                    # Ollama is down
                    if self._last_status == "healthy" or self._last_status == "starting":
                        logger.warning(f"[OllamaManager] WARNING: Ollama health check failed!")

                    self._last_status = "unhealthy"

                    # Try to restart if we haven't exceeded max attempts
                    if self._restart_count < self.MAX_RESTART_ATTEMPTS:
                        self._restart_count += 1
                        logger.info(f"[OllamaManager] Attempting restart ({self._restart_count}/{self.MAX_RESTART_ATTEMPTS})...")

                        # Use single_check=True to avoid blocking loop
                        success = await self.start_and_verify(single_check=True)

                        if success:
                            logger.info(f"[OllamaManager] Restart successful")
                            self._restart_count = 0
                        else:
                            logger.info(f"[OllamaManager] Restart attempt completed (will verify on next health check)")

                    else:
                        if self._last_status != "max_restarts_exceeded":
                            logger.warning(f"[OllamaManager] WARNING: Max restart attempts exceeded")
                            logger.info(f"[OllamaManager] Please manually check Ollama")
                            self._last_status = "max_restarts_exceeded"

                # Wait for next check
                await asyncio.sleep(self.HEALTH_CHECK_INTERVAL)

            except asyncio.CancelledError:
                logger.info(f"[OllamaManager] Health check loop cancelled")
                break
            except Exception as e:
                logger.error(f"[OllamaManager] Error in health check loop: {e}")
                await asyncio.sleep(self.HEALTH_CHECK_INTERVAL)

    async def start_monitoring(self):
        """Start health monitoring in background (periodic health checks only)"""
        if self._running:
            logger.info(f"[OllamaManager] Monitoring already running")
            return

        self._running = True

        # Skip initial startup if already healthy (start_and_verify_simple already called)
        if self._last_status != "healthy":
            # Only try to start if not already healthy
            await self.start_and_verify(single_check=True)

        # Start background health check loop
        self._health_task = asyncio.create_task(self._health_check_loop())
        logger.info(f"[OllamaManager] Periodic health monitoring started (interval: {self.HEALTH_CHECK_INTERVAL}s)")

    async def stop_monitoring(self):
        """Stop health monitoring"""
        if not self._running:
            return

        logger.info(f"[OllamaManager] Stopping monitoring...")
        self._running = False

        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass

        logger.info(f"[OllamaManager] Monitoring stopped")

    def cleanup(self):
        """Kill the Ollama server process if we started it"""
        try:
            if self._process and self._process.poll() is None:
                # Process is still running
                logger.info(f"[OllamaManager] Terminating Ollama server (PID: {self._process.pid})...")
                self._process.terminate()

                # Wait up to 3 seconds for graceful shutdown
                try:
                    self._process.wait(timeout=3)
                    logger.info(f"[OllamaManager] Ollama server stopped gracefully")
                except subprocess.TimeoutExpired:
                    # Force kill if it doesn't stop gracefully
                    logger.info(f"[OllamaManager] Force killing Ollama server...")
                    self._process.kill()
                    self._process.wait()
                    logger.info(f"[OllamaManager] Ollama server killed")
            else:
                logger.info(f"[OllamaManager] No Ollama process to clean up")
        except Exception as e:
            logger.error(f"[OllamaManager] Error during cleanup: {e}")

    def get_status(self) -> dict:
        """
        Get current Ollama status
        Returns: {status, last_check_time, restart_count}
        """
        return {
            "status": self._last_status,
            "last_check_time": self._last_check_time,
            "restart_count": self._restart_count,
            "monitoring_active": self._running,
            "health_check_interval": self.HEALTH_CHECK_INTERVAL
        }


# Global singleton
_ollama_manager: Optional[OllamaManager] = None


def get_ollama_manager() -> OllamaManager:
    """Get or create global OllamaManager instance"""
    global _ollama_manager
    if _ollama_manager is None:
        _ollama_manager = OllamaManager()
    return _ollama_manager
