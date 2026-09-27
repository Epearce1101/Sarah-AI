"""
Test script for production vision service
Tests all modes with a sample image
"""

import requests
from sarah_api_auth import backend_headers
import sys
import time
from pathlib import Path
import io
from PIL import Image, ImageDraw, ImageFont


def create_test_image():
    """Create a simple test image with text"""
    img = Image.new('RGB', (800, 600), color='white')
    draw = ImageDraw.Draw(img)

    # Draw some text
    try:
        font = ImageFont.truetype("arial.ttf", 40)
    except:
        font = ImageFont.load_default()

    draw.text((50, 50), "Hello, Vision AI!", fill='black', font=font)
    draw.text((50, 150), "This is a test image.", fill='blue', font=font)
    draw.rectangle([50, 250, 750, 450], outline='red', width=3)
    draw.text((60, 280), "def hello():\n    print('Hello World')", fill='green', font=font)

    # Save to bytes
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    return buffer.getvalue()


def test_health():
    """Test /health/vision endpoint"""
    print("=" * 70)
    print("TEST 1: Vision Health Check")
    print("=" * 70)

    try:
        response = requests.get("http://127.0.0.1:8907/health/vision", headers=backend_headers(), timeout=5)
        result = response.json()

        print(f"Status: {response.status_code}")
        print(f"Ollama Reachable: {result.get('ollama_reachable')}")
        print(f"Vision Ready: {result.get('vision_ready')}")
        print(f"Model: {result.get('model')}")
        print(f"Warmup Done: {result.get('warmup_done')}")
        print(f"Last Warmup: {result.get('last_warmup_ms')}ms")

        if result.get('last_error'):
            print(f"⚠️  Error: {result.get('last_error')}")

        if result.get('vision_ready'):
            print("✅ Vision service is READY")
            return True
        else:
            print("❌ Vision service NOT ready")
            return False

    except Exception as e:
        print(f"❌ Health check failed: {e}")
        return False


def test_analyze(mode="general"):
    """Test /vision/analyze endpoint"""
    print("\n" + "=" * 70)
    print(f"TEST 2: Vision Analysis (mode={mode})")
    print("=" * 70)

    try:
        # Create test image
        print("Creating test image...")
        image_bytes = create_test_image()
        print(f"Image size: {len(image_bytes)} bytes")

        # Prepare request
        files = {'file': ('test.png', image_bytes, 'image/png')}
        data = {'mode': mode}

        print(f"Sending request (mode={mode})...")
        start = time.time()

        response = requests.post(
            "http://127.0.0.1:8907/vision/analyze",
            headers=backend_headers(),
            files=files,
            data=data,
            timeout=120
        )

        elapsed = time.time() - start

        if response.status_code != 200:
            print(f"❌ Request failed: HTTP {response.status_code}")
            print(f"Error: {response.text[:300]}")
            return False

        result = response.json()

        print(f"\n✅ Analysis successful!")
        print(f"Timing: {result.get('timing_ms')}ms (total: {elapsed:.2f}s)")
        print(f"Model: {result.get('model')}")
        print(f"Mode: {result.get('mode')}")

        # Preprocessing info
        if 'preprocessing' in result:
            prep = result['preprocessing']
            print(f"\nPreprocessing:")
            print(f"  Original: {prep.get('original_size')} bytes")
            print(f"  Processed: {prep.get('processed_size')} bytes")
            print(f"  Format: {prep.get('format')}")

        print(f"\n--- Analysis Result ---")
        print(result.get('analysis', 'No analysis text')[:500])
        print("..." if len(result.get('analysis', '')) > 500 else "")
        print("-" * 70)

        return True

    except requests.Timeout:
        print("❌ Request timeout (120s)")
        return False
    except Exception as e:
        print(f"❌ Analysis failed: {e}")
        return False


def test_invalid_file():
    """Test with invalid file type"""
    print("\n" + "=" * 70)
    print("TEST 3: Invalid File Type (should fail gracefully)")
    print("=" * 70)

    try:
        # Send text file as image
        files = {'file': ('test.txt', b'This is not an image', 'text/plain')}
        data = {'mode': 'general'}

        response = requests.post(
            "http://127.0.0.1:8907/vision/analyze",
            headers=backend_headers(),
            files=files,
            data=data,
            timeout=10
        )

        if response.status_code == 400:
            print("✅ Correctly rejected invalid file type")
            print(f"Error message: {response.json().get('detail', 'No detail')}")
            return True
        else:
            print(f"❌ Unexpected status: {response.status_code}")
            return False

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False


def test_all_modes():
    """Test all analysis modes"""
    print("\n" + "=" * 70)
    print("TEST 4: All Analysis Modes")
    print("=" * 70)

    modes = ["general", "ocr", "ui", "code"]
    results = []

    for mode in modes:
        print(f"\n--- Testing mode: {mode} ---")
        try:
            image_bytes = create_test_image()
            files = {'file': ('test.png', image_bytes, 'image/png')}
            data = {'mode': mode}

            response = requests.post(
                "http://127.0.0.1:8907/vision/analyze",
                headers=backend_headers(),
                files=files,
                data=data,
                timeout=120
            )

            if response.status_code == 200:
                result = response.json()
                print(f"✅ {mode}: {result.get('timing_ms')}ms")
                print(f"   Output: {result.get('analysis', '')[:100]}...")
                results.append(True)
            else:
                print(f"❌ {mode}: HTTP {response.status_code}")
                results.append(False)

        except Exception as e:
            print(f"❌ {mode}: {e}")
            results.append(False)

    passed = sum(results)
    total = len(results)
    print(f"\n--- Results: {passed}/{total} modes passed ---")

    return all(results)


def main():
    """Run all tests"""
    print("\n" + "🔬" * 35)
    print("SARAH AI - PRODUCTION VISION SERVICE TEST")
    print("🔬" * 35 + "\n")

    results = []

    # Test 1: Health check
    results.append(("Health Check", test_health()))

    if not results[-1][1]:
        print("\n⚠️  Skipping remaining tests (vision service not ready)")
        print("\nTroubleshooting:")
        print("  1. Start backend: python start.py")
        print("  2. Check Ollama: curl http://localhost:11434")
        print("  3. Check model: ollama list | findstr qwen3-vl")
        return 1

    # Test 2: Basic analysis
    results.append(("Basic Analysis", test_analyze("general")))

    # Test 3: Invalid file
    results.append(("Invalid File", test_invalid_file()))

    # Test 4: All modes
    results.append(("All Modes", test_all_modes()))

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {test_name}")

    print("=" * 70)

    if all(passed for _, passed in results):
        print("\n🎉 ALL TESTS PASSED!")
        print("\nVision service is ready for production use.")
        return 0
    else:
        print("\n⚠️  SOME TESTS FAILED")
        print("\nCheck errors above for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
