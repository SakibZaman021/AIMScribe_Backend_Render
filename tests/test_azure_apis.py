"""
AIMScribe API Testing Script
Run this to verify Azure OpenAI APIs are working correctly.

Usage:
    python tests/test_azure_apis.py
"""

import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def print_header(title):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)

def print_result(success, message):
    status = "✅ PASS" if success else "❌ FAIL"
    print(f"{status}: {message}")

def test_environment_variables():
    """Test 1: Check if all required environment variables are set."""
    print_header("TEST 1: Environment Variables")

    required_vars = [
        ("AZURE_TRANSCRIBE_ENDPOINT", "Transcription API Endpoint"),
        ("AZURE_TRANSCRIBE_API_KEY", "Transcription API Key"),
        ("AZURE_TRANSCRIBE_DEPLOYMENT", "Transcription Deployment Name"),
        ("AZURE_NER_ENDPOINT", "NER API Endpoint"),
        ("AZURE_NER_API_KEY", "NER API Key"),
        ("AZURE_NER_DEPLOYMENT", "NER Deployment Name"),
    ]

    all_set = True
    for var, description in required_vars:
        value = os.getenv(var, "")
        if value and "your-" not in value.lower():
            print_result(True, f"{description} ({var})")
        else:
            print_result(False, f"{description} ({var}) - NOT SET or placeholder")
            all_set = False

    return all_set


def test_ner_api():
    """Test 2: Test NER API (GPT-4/4o) with a simple request."""
    print_header("TEST 2: NER API (GPT-4o)")

    try:
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=os.getenv("AZURE_NER_ENDPOINT"),
            api_key=os.getenv("AZURE_NER_API_KEY"),
            api_version=os.getenv("AZURE_API_VERSION", "2024-02-15-preview")
        )

        print("  Sending test request to NER API...")

        response = client.chat.completions.create(
            model=os.getenv("AZURE_NER_DEPLOYMENT"),
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say 'NER API is working!' in Bengali."}
            ],
            temperature=0.1,
            max_tokens=50
        )

        result = response.choices[0].message.content
        print(f"  Response: {result}")
        print_result(True, "NER API is working!")
        return True

    except Exception as e:
        print_result(False, f"NER API Error: {e}")
        return False


def test_transcription_api():
    """Test 3: Test Transcription API (GPT-4o-transcribe) with a simple text request."""
    print_header("TEST 3: Transcription API (GPT-4o-transcribe)")

    try:
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=os.getenv("AZURE_TRANSCRIBE_ENDPOINT"),
            api_key=os.getenv("AZURE_TRANSCRIBE_API_KEY"),
            api_version=os.getenv("AZURE_API_VERSION", "2024-02-15-preview")
        )

        print("  Sending test request to Transcription API...")

        # First, test if the model responds (without audio)
        response = client.chat.completions.create(
            model=os.getenv("AZURE_TRANSCRIBE_DEPLOYMENT"),
            messages=[
                {"role": "system", "content": "You are a Bengali transcription expert."},
                {"role": "user", "content": "Say 'Transcription API is ready!' in Bengali."}
            ],
            temperature=0.1,
            max_tokens=50
        )

        result = response.choices[0].message.content
        print(f"  Response: {result}")
        print_result(True, "Transcription API is working!")
        return True

    except Exception as e:
        print_result(False, f"Transcription API Error: {e}")
        return False


def test_transcription_with_audio():
    """Test 4: Test actual audio transcription (requires test audio file)."""
    print_header("TEST 4: Audio Transcription (with test file)")

    test_audio_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tests",
        "test_audio.wav"
    )

    if not os.path.exists(test_audio_path):
        print(f"  ⚠️  SKIP: No test audio file found at {test_audio_path}")
        print("  To test audio transcription, place a .wav file at: tests/test_audio.wav")
        return None

    try:
        import base64
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=os.getenv("AZURE_TRANSCRIBE_ENDPOINT"),
            api_key=os.getenv("AZURE_TRANSCRIBE_API_KEY"),
            api_version=os.getenv("AZURE_API_VERSION", "2024-02-15-preview")
        )

        # Read and encode audio
        with open(test_audio_path, "rb") as f:
            audio_base64 = base64.b64encode(f.read()).decode("utf-8")

        print(f"  Audio file loaded: {os.path.getsize(test_audio_path) / 1024:.2f} KB")
        print("  Sending audio to GPT-4o-transcribe...")

        response = client.chat.completions.create(
            model=os.getenv("AZURE_TRANSCRIBE_DEPLOYMENT"),
            messages=[
                {
                    "role": "system",
                    "content": """You are a Bengali medical transcription expert.

## SPEAKER LABELS
- [ডাক্তার]: Doctor's speech
- [রোগী]: Patient's speech
- [রোগীর সাথী]: Patient's companion

Transcribe the audio with speaker labels."""
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Please transcribe this Bengali medical conversation."
                        },
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": audio_base64,
                                "format": "wav"
                            }
                        }
                    ]
                }
            ],
            temperature=0.1,
            max_tokens=2000
        )

        transcript = response.choices[0].message.content
        print(f"\n  Transcript:\n  {'-' * 40}")
        for line in transcript.split('\n')[:10]:  # Show first 10 lines
            print(f"  {line}")
        print(f"  {'-' * 40}")

        print_result(True, "Audio transcription working!")
        return True

    except Exception as e:
        print_result(False, f"Audio transcription error: {e}")
        return False


def test_database_connection():
    """Test 5: Test PostgreSQL connection."""
    print_header("TEST 5: PostgreSQL Database")

    try:
        import psycopg2

        conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", 5432)),
            dbname=os.getenv("POSTGRES_DB", "aimscribe_db"),
            user=os.getenv("POSTGRES_USER", "aimscribe_user"),
            password=os.getenv("POSTGRES_PASSWORD", "")
        )

        cursor = conn.cursor()
        cursor.execute("SELECT version();")
        version = cursor.fetchone()[0]
        print(f"  PostgreSQL: {version[:50]}...")

        cursor.close()
        conn.close()

        print_result(True, "PostgreSQL connection successful!")
        return True

    except Exception as e:
        print_result(False, f"PostgreSQL Error: {e}")
        print("  Make sure PostgreSQL is running and credentials are correct.")
        return False


def test_redis_connection():
    """Test 6: Test Redis connection."""
    print_header("TEST 6: Redis")

    try:
        import redis

        client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=int(os.getenv("REDIS_DB", 0)),
            password=os.getenv("REDIS_PASSWORD") or None
        )

        # Test ping
        client.ping()

        # Test set/get
        client.set("aimscribe:test", "working")
        value = client.get("aimscribe:test")
        client.delete("aimscribe:test")

        print_result(True, "Redis connection successful!")
        return True

    except Exception as e:
        print_result(False, f"Redis Error: {e}")
        print("  Make sure Redis is running.")
        return False


def test_minio_connection():
    """Test 7: Test MinIO connection."""
    print_header("TEST 7: MinIO (Object Storage)")

    try:
        from minio import Minio

        client = Minio(
            os.getenv("MINIO_ENDPOINT", "localhost:9000"),
            access_key=os.getenv("MINIO_ACCESS_KEY", "aimscribe"),
            secret_key=os.getenv("MINIO_SECRET_KEY", "aimscribe123"),
            secure=os.getenv("MINIO_SECURE", "false").lower() == "true"
        )

        # Check if bucket exists
        bucket = os.getenv("MINIO_BUCKET", "aimscribe-audio")
        exists = client.bucket_exists(bucket)

        if exists:
            print(f"  Bucket '{bucket}' exists")
        else:
            print(f"  Bucket '{bucket}' does not exist, creating...")
            client.make_bucket(bucket)
            print(f"  Bucket '{bucket}' created")

        print_result(True, "MinIO connection successful!")
        return True

    except Exception as e:
        print_result(False, f"MinIO Error: {e}")
        print("  Make sure MinIO is running.")
        return False


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("       AIMScribe Backend - API & Services Test")
    print("=" * 60)

    results = {
        "Environment Variables": test_environment_variables(),
        "NER API (GPT-4o)": test_ner_api(),
        "Transcription API": test_transcription_api(),
        "Audio Transcription": test_transcription_with_audio(),
        "PostgreSQL": test_database_connection(),
        "Redis": test_redis_connection(),
        "MinIO": test_minio_connection(),
    }

    # Summary
    print_header("TEST SUMMARY")

    passed = 0
    failed = 0
    skipped = 0

    for test_name, result in results.items():
        if result is True:
            print(f"  ✅ {test_name}")
            passed += 1
        elif result is False:
            print(f"  ❌ {test_name}")
            failed += 1
        else:
            print(f"  ⚠️  {test_name} (SKIPPED)")
            skipped += 1

    print(f"\n  Total: {passed} passed, {failed} failed, {skipped} skipped")

    if failed == 0:
        print("\n  🎉 All tests passed! Your backend is ready.")
    else:
        print("\n  ⚠️  Some tests failed. Please check the configuration.")

    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
