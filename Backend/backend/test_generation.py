"""
Test Generation System
=====================

Automatically generate unit tests, integration tests, and run them in a sandbox.
"""

import os
import re
import ast
import json
import logging
import subprocess
import pathlib
import shutil
import sys
import tempfile
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from enum import Enum

import requests

from backend.config import settings as _settings
from backend.utils.proc_jail import minimal_env, run_jailed

logger = logging.getLogger("sarah.test_generation")


class TestType(str, Enum):
    """Types of tests to generate."""
    UNIT = "unit"
    INTEGRATION = "integration"
    E2E = "e2e"
    PROPERTY = "property"


class TestFramework(str, Enum):
    """Supported test frameworks."""
    PYTEST = "pytest"
    UNITTEST = "unittest"
    JEST = "jest"
    MOCHA = "mocha"
    VITEST = "vitest"


@dataclass
class TestCase:
    """Represents a single test case."""
    name: str
    description: str
    test_code: str
    framework: TestFramework
    test_type: TestType
    setup_code: str = ""
    teardown_code: str = ""
    fixtures: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)


@dataclass
class TestSuite:
    """Represents a complete test suite for a module."""
    module_name: str
    file_path: str
    framework: TestFramework
    test_cases: List[TestCase]
    coverage_targets: List[str] = field(default_factory=list)
    setup_code: str = ""
    teardown_code: str = ""


# ---------------------------------------------------------
# CODE ANALYSIS FOR TEST GENERATION
# ---------------------------------------------------------

def extract_functions_from_python(file_path: str) -> List[Dict[str, Any]]:
    """
    Extract function definitions from a Python file.

    Returns:
        List of dicts with function name, args, docstring, etc.
    """
    if not os.path.exists(file_path):
        return []

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=file_path)
    except Exception as e:
        logger.warning(f"Failed to parse {file_path}: {e}")
        return []

    functions = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            # Extract function signature
            args = [arg.arg for arg in node.args.args]

            # Extract docstring
            docstring = ast.get_docstring(node) or ""

            # Extract decorators
            decorators = [d.id if isinstance(d, ast.Name) else str(d) for d in node.decorator_list]

            # Detect if it's async
            is_async = isinstance(node, ast.AsyncFunctionDef)

            functions.append({
                "name": node.name,
                "args": args,
                "docstring": docstring,
                "decorators": decorators,
                "is_async": is_async,
                "lineno": node.lineno
            })

    return functions


def extract_classes_from_python(file_path: str) -> List[Dict[str, Any]]:
    """
    Extract class definitions from a Python file.

    Returns:
        List of dicts with class name, methods, etc.
    """
    if not os.path.exists(file_path):
        return []

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=file_path)
    except Exception as e:
        logger.warning(f"Failed to parse {file_path}: {e}")
        return []

    classes = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            # Extract methods
            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append({
                        "name": item.name,
                        "args": [arg.arg for arg in item.args.args],
                        "is_async": isinstance(item, ast.AsyncFunctionDef)
                    })

            # Extract docstring
            docstring = ast.get_docstring(node) or ""

            classes.append({
                "name": node.name,
                "methods": methods,
                "docstring": docstring,
                "lineno": node.lineno
            })

    return classes


# ---------------------------------------------------------
# AI-POWERED TEST GENERATION
# ---------------------------------------------------------

def _call_llm_test_generation(
    code: str,
    language: str,
    test_type: TestType,
    framework: TestFramework,
    context: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Use the configured LLM (OpenRouter) to generate comprehensive tests for code.

    Args:
        code: Source code to generate tests for
        language: Programming language
        test_type: Type of tests (unit, integration, e2e)
        framework: Test framework to use
        context: Optional context about the project

    Returns:
        Generated test suite
    """
    api_key = _settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    model = _settings.openrouter_model
    url = f"{_settings.openrouter_base_url}/chat/completions"

    system_prompt = f"""You are Sarah's Test Generator, an expert at writing comprehensive tests.

Your job: Generate high-quality {test_type} tests using {framework}.

Respond with STRICT JSON ONLY (no markdown, no comments):
{{
  "test_suite_name": string,      // Name of the test suite
  "imports": string[],            // Required imports
  "setup_code": string,           // Setup code for all tests
  "teardown_code": string,        // Teardown code for all tests
  "test_cases": [
    {{
      "name": string,              // Test case name (test_function_name_scenario)
      "description": string,       // What this test verifies
      "test_code": string,         // Complete test code
      "setup_code": string,        // Setup for this specific test
      "teardown_code": string,     // Teardown for this test
      "fixtures": string[],        // Required fixtures
      "expected_result": string,   // What should happen
      "edge_cases_covered": string[]  // Edge cases this test covers
    }}
  ],
  "coverage_notes": string,       // Notes about test coverage
  "missing_tests": string[]       // Tests that should be added later
}}

Test quality requirements:
1. Follow AAA pattern: Arrange, Act, Assert
2. Test happy path + edge cases + error cases
3. Use descriptive test names (test_function_scenario_expectedResult)
4. Include docstrings explaining what is tested
5. Use appropriate assertions
6. Mock external dependencies
7. Ensure tests are independent (no shared state)
8. Add parametrized tests for multiple inputs"""

    user_prompt = f"""Generate {test_type} tests for this {language} code using {framework}:

CODE:
```{language}
{code}
```"""

    if context:
        user_prompt += f"\n\nCONTEXT:\n{json.dumps(context, indent=2)}"

    user_prompt += f"""

Generate comprehensive tests that:
1. Test normal functionality
2. Test edge cases (empty inputs, None, zero, negative numbers, etc.)
3. Test error handling (invalid inputs, exceptions)
4. Test boundary conditions
5. Are well-documented and maintainable

Framework: {framework}
Test Type: {test_type}"""

    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 8192,
        "temperature": 0.2,
    }

    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=90,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices") or []
        if not choices:
            return None

        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, str):
            return json.loads(content.strip())

        return None
    except Exception as exc:
        logger.warning(f"LLM test generation failed: {exc}")
        return None


def generate_tests(
    code: str,
    language: str = "python",
    test_type: TestType = TestType.UNIT,
    framework: Optional[TestFramework] = None,
    use_ai: bool = True
) -> Optional[TestSuite]:
    """
    Generate tests for the given code.

    Args:
        code: Source code to test
        language: Programming language
        test_type: Type of tests to generate
        framework: Test framework (auto-detected if None)
        use_ai: Whether to use AI for generation

    Returns:
        TestSuite or None
    """
    # Auto-detect framework if not specified
    if framework is None:
        if language in ["python", "py"]:
            framework = TestFramework.PYTEST
        elif language in ["javascript", "js", "typescript", "ts"]:
            framework = TestFramework.JEST
        else:
            framework = TestFramework.PYTEST

    if use_ai:
        result = _call_llm_test_generation(code, language, test_type, framework)
        if result:
            # Convert to TestCase objects
            test_cases = []
            for case_data in result.get("test_cases", []):
                test_case = TestCase(
                    name=case_data.get("name", "test_unknown"),
                    description=case_data.get("description", ""),
                    test_code=case_data.get("test_code", ""),
                    framework=framework,
                    test_type=test_type,
                    setup_code=case_data.get("setup_code", ""),
                    teardown_code=case_data.get("teardown_code", ""),
                    fixtures=case_data.get("fixtures", []),
                )
                test_cases.append(test_case)

            return TestSuite(
                module_name=result.get("test_suite_name", "test_module"),
                file_path="",  # Will be set later
                framework=framework,
                test_cases=test_cases,
                setup_code=result.get("setup_code", ""),
                teardown_code=result.get("teardown_code", "")
            )

    return None


def format_test_suite(suite: TestSuite) -> str:
    """
    Format test suite into actual test file content.

    Args:
        suite: TestSuite to format

    Returns:
        Complete test file content
    """
    lines = []

    # Add file header
    lines.append(f'"""')
    lines.append(f'Test suite for {suite.module_name}')
    lines.append(f'Generated by Sarah AI')
    lines.append(f'"""')
    lines.append('')

    # Add imports based on framework
    if suite.framework == TestFramework.PYTEST:
        lines.append('import pytest')
    elif suite.framework == TestFramework.UNITTEST:
        lines.append('import unittest')

    lines.append('')

    # Add setup code
    if suite.setup_code:
        lines.append(suite.setup_code)
        lines.append('')

    # Add test cases
    for test_case in suite.test_cases:
        lines.append(f'def {test_case.name}():')
        if test_case.description:
            lines.append(f'    """')
            lines.append(f'    {test_case.description}')
            lines.append(f'    """')

        if test_case.setup_code:
            # Indent setup code
            for line in test_case.setup_code.splitlines():
                lines.append(f'    {line}')
            lines.append('')

        # Indent test code
        for line in test_case.test_code.splitlines():
            lines.append(f'    {line}')

        if test_case.teardown_code:
            lines.append('')
            for line in test_case.teardown_code.splitlines():
                lines.append(f'    {line}')

        lines.append('')
        lines.append('')

    # Add teardown code
    if suite.teardown_code:
        lines.append(suite.teardown_code)

    return '\n'.join(lines)


# ---------------------------------------------------------
# TEST EXECUTION IN SANDBOX
# ---------------------------------------------------------

@dataclass
class TestResult:
    """Result of running tests."""
    success: bool
    total_tests: int
    passed_tests: int
    failed_tests: int
    skipped_tests: int
    duration_seconds: float
    output: str
    errors: List[str] = field(default_factory=list)
    coverage_percent: Optional[float] = None


def run_tests_in_sandbox(
    test_file_path: str,
    framework: TestFramework = TestFramework.PYTEST,
    timeout: int = 60,
    with_coverage: bool = False
) -> TestResult:
    """
    Run tests in a sandboxed environment.

    Args:
        test_file_path: Path to test file
        framework: Test framework being used
        timeout: Maximum time to run tests (seconds)
        with_coverage: Whether to collect coverage data

    Returns:
        TestResult with execution results
    """
    if not os.path.exists(test_file_path):
        return TestResult(
            success=False,
            total_tests=0,
            passed_tests=0,
            failed_tests=0,
            skipped_tests=0,
            duration_seconds=0.0,
            output="",
            errors=[f"Test file not found: {test_file_path}"]
        )

    # Build command based on framework. Use this interpreter rather than
    # whatever `pytest`/`python` happens to be first on PATH.
    extra_path = [pathlib.Path(sys.executable).parent]
    if framework == TestFramework.PYTEST:
        cmd = [sys.executable, "-m", "pytest", test_file_path, "-v", "--tb=short"]
        if with_coverage:
            cmd.extend(["--cov", "--cov-report=term"])
    elif framework == TestFramework.UNITTEST:
        cmd = [sys.executable, "-m", "unittest", test_file_path]
    elif framework == TestFramework.JEST:
        npm = str(_settings.npm_path) if os.path.exists(str(_settings.npm_path)) else (
            shutil.which("npm.cmd") or shutil.which("npm")
        )
        if not npm:
            return TestResult(
                success=False, total_tests=0, passed_tests=0, failed_tests=0,
                skipped_tests=0, duration_seconds=0.0, output="",
                errors=["npm not found"],
            )
        extra_path.append(pathlib.Path(npm).parent)
        cmd = (["cmd", "/c", npm] if npm.lower().endswith((".cmd", ".bat")) else [npm]) + [
            "test", "--", test_file_path,
        ]
    else:
        return TestResult(
            success=False,
            total_tests=0,
            passed_tests=0,
            failed_tests=0,
            skipped_tests=0,
            duration_seconds=0.0,
            output="",
            errors=[f"Unsupported framework: {framework}"]
        )

    # Tests need the project's own files, so there is no filesystem jail here;
    # what they do get is a Job Object (memory cap, whole-tree kill on
    # timeout, no clipboard) and an environment without API keys/tokens.
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="sarah_tests_"))
    try:
        import time
        start_time = time.time()

        env = minimal_env(scratch, extra_path)
        env["APPDATA"] = env["LOCALAPPDATA"] = str(scratch)
        result = run_jailed(
            cmd,
            cwd=pathlib.Path(os.path.dirname(os.path.abspath(test_file_path))),
            env=env,
            timeout=max(1, min(int(timeout), 900)),
            memory_mb=2048,
            max_processes=32,
            max_output_bytes=2_000_000,
            wait_for_release=False,
        )

        duration = time.time() - start_time
        if result.timed_out:
            return TestResult(
                success=False,
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                skipped_tests=0,
                duration_seconds=duration,
                output=result.stdout + "\n" + result.stderr,
                errors=[f"Tests timed out after {timeout} seconds"]
            )
        output = result.stdout + "\n" + result.stderr

        # Parse output to extract test counts
        total = passed = failed = skipped = 0
        coverage = None

        if framework == TestFramework.PYTEST:
            # Parse pytest output
            # Example: "5 passed, 1 failed, 1 skipped in 0.42s"
            match = re.search(r'(\d+) passed', output)
            if match:
                passed = int(match.group(1))

            match = re.search(r'(\d+) failed', output)
            if match:
                failed = int(match.group(1))

            match = re.search(r'(\d+) skipped', output)
            if match:
                skipped = int(match.group(1))

            total = passed + failed + skipped

            # Parse coverage
            match = re.search(r'TOTAL\s+\d+\s+\d+\s+(\d+)%', output)
            if match:
                coverage = float(match.group(1))

        success = result.returncode == 0

        return TestResult(
            success=success,
            total_tests=total,
            passed_tests=passed,
            failed_tests=failed,
            skipped_tests=skipped,
            duration_seconds=duration,
            output=output,
            errors=[] if success else ["Tests failed"],
            coverage_percent=coverage
        )

    except Exception as e:
        return TestResult(
            success=False,
            total_tests=0,
            passed_tests=0,
            failed_tests=0,
            skipped_tests=0,
            duration_seconds=0.0,
            output="",
            errors=[f"Test execution failed: {str(e)}"]
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def format_test_result(result: TestResult) -> str:
    """Format test result into human-readable text."""
    lines = ["🧪 TEST RESULTS\n"]

    if result.success:
        lines.append("✅ All tests passed!")
    else:
        lines.append("❌ Some tests failed")

    lines.append(f"\nTotal Tests: {result.total_tests}")
    lines.append(f"Passed: ✅ {result.passed_tests}")
    if result.failed_tests > 0:
        lines.append(f"Failed: ❌ {result.failed_tests}")
    if result.skipped_tests > 0:
        lines.append(f"Skipped: ⏭️ {result.skipped_tests}")

    lines.append(f"\nDuration: {result.duration_seconds:.2f}s")

    if result.coverage_percent is not None:
        lines.append(f"Coverage: {result.coverage_percent:.1f}%")

    if result.errors:
        lines.append("\n⚠️ Errors:")
        for error in result.errors:
            lines.append(f"  • {error}")

    if result.output:
        lines.append("\n📝 Output:")
        lines.append("```")
        lines.append(result.output[:1000])  # Limit output
        if len(result.output) > 1000:
            lines.append("... (output truncated)")
        lines.append("```")

    return "\n".join(lines)
