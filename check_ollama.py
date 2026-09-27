"""
Quick test to verify Ollama is working as primary vision service
"""

import requests
from sarah_api_auth import backend_headers
import sys


def test_ollama_running():
    """Test if Ollama is running"""
    print("=" * 60)
    print("TEST 1: Is Ollama Running?")
    print("=" * 60)

    try:
        response = requests.get("http://localhost:11434", timeout=2)
        print("✅ Ollama is running")
        return True
    except:
        print("❌ Ollama is NOT running")
        print("   Start it: ollama serve")
        return False


def test_model_available():
    """Test if qwen3-vl:8b is available"""
    print("\n" + "=" * 60)
    print("TEST 2: Is qwen3-vl:8b Model Available?")
    print("=" * 60)

    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=5)
        data = response.json()
        models = [m["name"] for m in data.get("models", [])]

        if "qwen3-vl:8b" in models:
            print("✅ qwen3-vl:8b model is available")
            print(f"   Total models: {len(models)}")
            return True
        else:
            print("❌ qwen3-vl:8b model NOT found")
            print(f"   Available models: {models}")
            print("   Download it: ollama pull qwen3-vl:8b")
            return False
    except Exception as e:
        print(f"❌ Error checking models: {e}")
        return False


def test_backend_ollama_endpoint():
    """Test if backend Ollama endpoint is responding"""
    print("\n" + "=" * 60)
    print("TEST 3: Backend Ollama Endpoint")
    print("=" * 60)

    try:
        response = requests.get("http://127.0.0.1:8907/api/ollama/health", headers=backend_headers(), timeout=5)
        result = response.json()

        if result.get("ok") and result.get("available"):
            print("✅ Backend Ollama endpoint is working")
            print(f"   Model available: {result.get('available')}")
            print(f"   Models: {result.get('models', [])[:3]}...")
            return True
        else:
            print("⚠️  Backend endpoint responding but model not available")
            print(f"   Response: {result}")
            return False
    except requests.ConnectionError:
        print("❌ Backend is NOT running")
        print("   Start it: start.bat")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_text_analysis():
    """Test text analysis endpoint"""
    print("\n" + "=" * 60)
    print("TEST 4: Text Analysis Endpoint")
    print("=" * 60)

    try:
        test_code = """def hello():
    print("Hello, World!")
    return 42"""

        response = requests.post(
            "http://127.0.0.1:8907/api/ollama/analyze-text",
            headers=backend_headers(),
            data={
                "text": test_code,
                "mode": "code",
                "language": "python"
            },
            timeout=30
        )

        if response.status_code == 200:
            result = response.json()
            print("✅ Text analysis endpoint working")
            print(f"   Timing: {result.get('timing_ms')}ms")
            print(f"   Analysis: {result.get('analysis', '')[:100]}...")
            return True
        else:
            print(f"❌ Endpoint returned {response.status_code}")
            print(f"   Error: {response.text[:200]}")
            return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def main():
    """Run all tests"""
    print("\n" + "🔬" * 30)
    print("SARAH AI - OLLAMA PRIMARY SERVICE TEST")
    print("🔬" * 30 + "\n")

    results = []

    # Test 1: Ollama running
    results.append(("Ollama Running", test_ollama_running()))

    # Test 2: Model available
    if results[-1][1]:
        results.append(("Model Available", test_model_available()))
    else:
        print("\n⚠️  Skipping remaining tests (Ollama not running)")
        results.append(("Model Available", False))
        results.append(("Backend Endpoint", False))
        results.append(("Text Analysis", False))

    # Test 3: Backend endpoint
    if results[-1][1]:
        results.append(("Backend Endpoint", test_backend_ollama_endpoint()))
    else:
        print("\n⚠️  Skipping backend tests (model not available)")
        results.append(("Backend Endpoint", False))
        results.append(("Text Analysis", False))

    # Test 4: Text analysis (requires backend)
    if results[-1][1]:
        results.append(("Text Analysis", test_text_analysis()))
    else:
        print("\n⚠️  Skipping text analysis test (backend not ready)")
        results.append(("Text Analysis", False))

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    all_passed = True
    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {test_name}")
        if not passed:
            all_passed = False

    print("=" * 60)

    if all_passed:
        print("\n🎉 ALL TESTS PASSED!")
        print("\nOllama is working as PRIMARY vision service.")
        print("You can now:")
        print("  1. Take screenshots and click '🔬 Analyze'")
        print("  2. Paste code snippets and click 'Analyze'")
        print("  3. Ollama will handle all vision/code analysis")
        print("  4. Gemini is available as backup")
        return 0
    else:
        print("\n⚠️  SOME TESTS FAILED")
        print("\nFixes:")
        if not results[0][1]:
            print("  1. Start Ollama: ollama serve")
        if not results[1][1]:
            print("  2. Download model: ollama pull qwen3-vl:8b")
        if not results[2][1]:
            print("  3. Start backend: start.bat")
        print("\nRun this test again after fixes.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
