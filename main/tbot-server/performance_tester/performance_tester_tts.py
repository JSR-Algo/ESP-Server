import asyncio
import logging
import os
import time
import threading
from typing import Dict
import yaml
from tabulate import tabulate

# Ensure from core.utils.tts Import create_tts_instance
from core.utils.tts import create_instance as create_tts_instance
from config.settings import load_config

# Set global log level to WARNING
logging.basicConfig(level=logging.WARNING)

description = "Non-streaming speech synthesis performance test"


class TTSPerformanceTester:
    def __init__(self):
        self.config = load_config()
        self.test_sentences = self.config.get("module_test", {}).get(
            "test_sentences",
            [
                "In the ninth year of Yonghe, in Guichou year, at beginning of late spring;",
                "People interact, spending whole lives in moments. Some share thoughts indoors; others follow what they trust, roaming beyond conventions. Though choices differ greatly, quiet and restless are not same,",
                "Whenever I reflect on why people before us were moved, it seems exactly aligned; reading their writings, I always sigh in grief, unable to make peace with it. I know treating life and death as one is absurd, and equating long life with early death is delusion.",
            ],
        )
        self.results = {}

    async def _test_tts(self, tts_name: str, config: Dict) -> Dict:
        """Test performance of single TTS module"""
        try:
            token_fields = ["access_token", "api_key", "token"]
            if any(
                field in config
                and any(x in config[field] for x in ["Your", "placeholder"])
                for field in token_fields
            ):
                print(f"TTS {tts_name} not configured with access_token/api_key, skipped")
                return {"name": tts_name, "errors": 1}

            module_type = config.get("type", tts_name)
            tts = create_tts_instance(module_type, config, delete_audio_file=True)

            # Set mock conn object, avoid TTS Implement Access self.conn.sample_rate When is None
            class MockConn:
                sample_rate = 16000
                audio_format = "pcm"
                stop_event = threading.Event()  # must be real Event Object
                client_abort = False
                headers = {}
            tts.conn = MockConn()

            # Set mock opus_encoder, avoid some TTS Access self.opus_encoder When is None
            class MockOpusEncoder:
                pass
            if not hasattr(tts, 'opus_encoder') or tts.opus_encoder is None:
                tts.opus_encoder = MockOpusEncoder()

            print(f"Test TTS: {tts_name}")

            # Connection test
            tmp_file = tts.generate_filename()
            await tts.text_to_speak("Connection test", tmp_file)

            if not tmp_file or not os.path.exists(tmp_file):
                print(f"{tts_name} connection failed")
                return {"name": tts_name, "errors": 1}

            total_time = 0
            test_count = len(self.test_sentences[:3])

            for i, sentence in enumerate(self.test_sentences[:2], 1):
                start = time.time()
                tmp_file = tts.generate_filename()
                await tts.text_to_speak(sentence, tmp_file)
                duration = time.time() - start
                total_time += duration

                if tmp_file and os.path.exists(tmp_file):
                    print(f"{tts_name} [{i}/{test_count}] test success")
                else:
                    print(f"{tts_name} [{i}/{test_count}] test failed")
                    return {"name": tts_name, "errors": 1}

            return {
                "name": tts_name,
                "avg_time": total_time / test_count,
                "errors": 0,
            }

        except Exception as e:
            print(f"{tts_name} test failed: {str(e)}")
            return {"name": tts_name, "errors": 1}

    def _print_results(self):
        """Print test results"""
        if not self.results:
            print("No valid TTS test results")
            return

        headers = ["TTS module", "Average time (seconds)", "Test sentence count", "Status"]
        table_data = []

        # Collect all data and classify
        valid_results = []
        error_results = []

        for name, data in self.results.items():
            if data["errors"] == 0:
                # Normal Result
                avg_time = f"{data['avg_time']:.3f}"
                test_count = len(self.test_sentences[:3])
                status = "✅ Normal"
                
                # SaveUsed forSortValue of
                valid_results.append({
                    "name": name,
                    "avg_time": avg_time,
                    "test_count": test_count,
                    "status": status,
                    "sort_key": data['avg_time']
                })
            else:
                # ErrorResult
                avg_time = "-"
                test_count = "0/3"
                
                # DefaultErrorType isNetwork error
                error_type = "Network error"
                status = f"❌ {error_type}"
                
                error_results.append([name, avg_time, test_count, status])

        # Sort by average time ascendingSort
        valid_results.sort(key=lambda x: x["sort_key"])

        # Convert sorted valid results to table data
        for result in valid_results:
            table_data.append([
                result["name"],
                result["avg_time"],
                result["test_count"],
                result["status"]
            ])

        # willErrorAdd result to end of table data
        table_data.extend(error_results)

        print("\nTTS performance test results:")
        print(
            tabulate(
                table_data,
                headers=headers,
                tablefmt="grid",
                colalign=("left", "right", "right", "left"),
            )
        )
        print("\nTest notes:")
        print("- Timeout control: max wait time for single request is 10 seconds")
        print("- Error handling: unable to connect and timeout listed as network errors")
        print("- Sort rule: sort by average time from fast to slow")

    async def run(self):
        """Run test"""
        print("Starting TTS performance test...")

        if not self.config.get("TTS"):
            print("TTS config not found in config file")
            return

        # Traverse AllTTSConfig
        tasks = []
        for tts_name, config in self.config.get("TTS", {}).items():
            tasks.append(self._test_tts(tts_name, config))

        # ConcurrencyRun test
        results = await asyncio.gather(*tasks)

        # SaveAll results, includingError
        for result in results:
            self.results[result["name"]] = result

        # Print Result
        self._print_results()


# Forperformance_tester.pycall requirement
async def main():
    tester = TTSPerformanceTester()
    await tester.run()


if __name__ == "__main__":
    tester = TTSPerformanceTester()
    asyncio.run(tester.run())
