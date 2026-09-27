"""
Test script for Ollama Vision API
Tests the /api/ollama/analyze endpoint with a sample image
"""

import requests
from sarah_api_auth import backend_headers
import base64
from pathlib import Path
import sys


def test_ollama_health():
    """Test if Ollama is running and model is available"""
    print("=" * 60)
    print("TEST 1: Ollama Health Check")
    print("=" * 60)

    try:
        response = requests.get("http://127.0.0.1:8907/api/ollama/health", headers=backend_headers(), timeout=5)
        result = response.json()

        print(f"Status Code: {response.status_code}")
        print(f"Response: {result}")

        if result.get("ok"):
            print("✅ Ollama is running")
            if result.get("available"):
                print(f"✅ Model available")
                print(f"   Available models: {result.get('models', [])}")
            else:
                print("⚠️  Model 'qwen3-vl:8b' not found")
                print("   Run: ollama pull qwen3-vl:8b")
        else:
            print(f"❌ Ollama health check failed: {result.get('error')}")

        return result.get("ok") and result.get("available")

    except requests.ConnectionError:
        print("❌ Cannot connect to backend. Is the server running?")
        print("   Start with: python backend/run_sarah_ai.py")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_ollama_warmup():
    """Test warming up the model"""
    print("\n" + "=" * 60)
    print("TEST 2: Model Warmup")
    print("=" * 60)

    try:
        response = requests.post("http://127.0.0.1:8907/api/ollama/warmup", headers=backend_headers(), timeout=30)
        result = response.json()

        print(f"Status Code: {response.status_code}")
        print(f"Response: {result}")

        if result.get("ok"):
            print("✅ Warmup successful")
        else:
            print(f"❌ Warmup failed: {result.get('error')}")

        return result.get("ok")

    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_ollama_analyze(image_path: str, mode: str = "general"):
    """Test analyzing an image"""
    print("\n" + "=" * 60)
    print(f"TEST 3: Image Analysis (mode: {mode})")
    print("=" * 60)

    try:
        # Check if file exists
        img_file = Path(image_path)
        if not img_file.exists():
            print(f"❌ Image file not found: {image_path}")
            return False

        print(f"Reading image: {img_file.name}")
        print(f"Size: {img_file.stat().st_size / 1024:.1f} KB")

        # Prepare multipart form data
        with open(img_file, 'rb') as f:
            files = {
                'file': (img_file.name, f, 'image/png')
            }
            data = {
                'mode': mode,
                'model': 'qwen3-vl:8b'
            }

            print(f"Sending request to /api/ollama/analyze...")
            response = requests.post(
                "http://127.0.0.1:8907/api/ollama/analyze",
                headers=backend_headers(),
                files=files,
                data=data,
                timeout=120
            )

        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print(f"✅ Analysis successful")
            print(f"   Model: {result.get('model')}")
            print(f"   Mode: {result.get('mode')}")
            print(f"   Timing: {result.get('timing_ms')}ms")
            print(f"\n{'='*60}")
            print("ANALYSIS RESULT:")
            print('='*60)
            print(result.get('analysis', ''))
            print('='*60)
            return True
        else:
            print(f"❌ Request failed: {response.text}")
            return False

    except requests.Timeout:
        print("❌ Request timed out (120s)")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def create_test_image():
    """Create a simple test image if none exists"""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new('RGB', (400, 300), color='white')
    draw = ImageDraw.Draw(img)

    # Draw some text
    draw.text((20, 20), "SARAH AI Vision Test", fill='black')
    draw.text((20, 60), "This is a test image", fill='blue')
    draw.text((20, 100), "for Ollama qwen3-vl:8b", fill='green')

    # Draw a rectangle
    draw.rectangle([20, 150, 380, 250], outline='red', width=3)
    draw.text((140, 190), "Test Box", fill='red')

    test_path = Path("test_image.png")
    img.save(test_path)
    print(f"✅ Created test image: {test_path.absolute()}")
    return str(test_path)


def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("SARAH AI - OLLAMA VISION TEST SUITE")
    print("=" * 60)
    print()

    # Test 1: Health check
    if not test_ollama_health():
        print("\n❌ Health check failed. Please ensure:")
        print("   1. Ollama is running (ollama serve)")
        print("   2. Model is downloaded (ollama pull qwen3-vl:8b)")
        print("   3. Backend server is running (python backend/run_sarah_ai.py)")
        return

    # Test 2: Warmup
    test_ollama_warmup()

    # Test 3: Analyze image
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
    else:
        print("\nNo image provided, creating test image...")
        try:
            image_path = create_test_image()
        except ImportError:
            print("⚠️  PIL not installed. Using a sample if available.")
            print("   Install: pip install Pillow")
            print("\nUsage: python test_ollama_vision.py <image_path>")
            return

    # Test different modes
    modes = ["general", "ocr", "ui", "code"]

    if len(sys.argv) > 2:
        # User specified mode
        mode = sys.argv[2]
        test_ollama_analyze(image_path, mode)
    else:
        # Test general mode only (others are optional)
        test_ollama_analyze(image_path, "general")

    print("\n" + "=" * 60)
    print("TESTS COMPLETE")
    print("=" * 60)
    print("\nTo test other modes:")
    print("  python test_ollama_vision.py <image> ocr    # Extract text")
    print("  python test_ollama_vision.py <image> ui     # Analyze UI")
    print("  python test_ollama_vision.py <image> code   # Extract code")


if __name__ == "__main__":
    main()
