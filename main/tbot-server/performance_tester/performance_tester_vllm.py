import time
import asyncio
import logging
import statistics
import base64
from typing import Dict
from tabulate import tabulate
from core.utils.vllm import create_instance
from config.settings import load_config

# Set global log level toWARNING, suppressINFOLevel Log
logging.basicConfig(level=logging.WARNING)

description = "Vision recognition model performance test"


class AsyncVisionPerformanceTester:
    def __init__(self):
        self.config = load_config()

        self.test_images = [
            "../../docs/images/demo1.png",
            "../../docs/images/demo2.png",
        ]
        self.test_questions = [
            "What is in this image?",
            "Please describe this image in detail",
        ]

        # Load test image
        self.results = {"vllm": {}}

    async def _test_vllm(self, vllm_name: str, config: Dict) -> Dict:
        """Async test single visual large model performance"""
        try:
            # CheckAPI keyConfig
            if "api_key" in config and any(
                x in config["api_key"] for x in ["Your", "placeholder", "sk-xxx"]
            ):
                print(f"⏭️  VLLM {vllm_name} not configured with api_key, skipped")
                return {"name": vllm_name, "type": "vllm", "errors": 1}

            # Get actual type (compatible with old config)
            module_type = config.get("type", vllm_name)
            vllm = create_instance(module_type, config)

            print(f"🖼️ Test VLLM: {vllm_name}")

            # Create all test tasks
            test_tasks = []
            for question in self.test_questions:
                for image in self.test_images:
                    test_tasks.append(
                        self._test_single_vision(vllm_name, vllm, question, image)
                    )

            # Run all tests concurrently
            test_results = await asyncio.gather(*test_tasks)

            # Processing Result
            valid_results = [r for r in test_results if r is not None]
            if not valid_results:
                print(f"⚠️  {vllm_name} no valid data, config may be wrong")
                return {"name": vllm_name, "type": "vllm", "errors": 1}

            response_times = [r["response_time"] for r in valid_results]

            # FilterExceptionData
            mean = statistics.mean(response_times)
            stdev = statistics.stdev(response_times) if len(response_times) > 1 else 0
            filtered_times = [t for t in response_times if t <= mean + 3 * stdev]

            if len(filtered_times) < len(test_tasks) * 0.5:
                print(f"⚠️  {vllm_name} insufficient valid data, network may be unstable")
                return {"name": vllm_name, "type": "vllm", "errors": 1}

            return {
                "name": vllm_name,
                "type": "vllm",
                "avg_response": sum(response_times) / len(response_times),
                "std_response": (
                    statistics.stdev(response_times) if len(response_times) > 1 else 0
                ),
                "errors": 0,
            }

        except Exception as e:
            print(f"⚠️ VLLM {vllm_name} test failed: {str(e)}")
            return {"name": vllm_name, "type": "vllm", "errors": 1}

    async def _test_single_vision(
        self, vllm_name: str, vllm, question: str, image: str
    ) -> Dict:
        """Test performance of single vision question"""
        try:
            print(f"📝 {vllm_name} start test: {question[:20]}...")
            start_time = time.time()

            # Read image and convert tobase64
            with open(image, "rb") as image_file:
                image_data = image_file.read()
                image_base64 = base64.b64encode(image_data).decode("utf-8")

            # Get DirectlyResponse
            response = vllm.response(question, image_base64)
            response_time = time.time() - start_time
            print(f"✓ {vllm_name} CompleteResponse: {response_time:.3f}s")

            return {
                "name": vllm_name,
                "type": "vllm",
                "response_time": response_time,
            }
        except Exception as e:
            print(f"⚠️ {vllm_name} test failed: {str(e)}")
            return None

    def _print_results(self):
        """Print test results"""
        vllm_table = []
        for name, data in self.results["vllm"].items():
            if data["errors"] == 0:
                stability = data["std_response"] / data["avg_response"]
                vllm_table.append(
                    [
                        name,
                        f"{data['avg_response']:.3f}seconds",
                        f"{stability:.3f}",
                    ]
                )

        if vllm_table:
            print("\nVision large model performance ranking:\n")
            print(
                tabulate(
                    vllm_table,
                    headers=["Model name", "ResponseTime cost", "Stability"],
                    tablefmt="github",
                    colalign=("left", "right", "right"),
                    disable_numparse=True,
                )
            )
        else:
            print("\n⚠️ No available vision large model for test.")

    async def run(self):
        """Execute full async test"""
        print("🔍 Start filtering available vision large models...")

        if not self.test_images:
            print(f"\n⚠️  {self.image_root} No image files under path, cannot test")
            return

        # Create all test tasks
        all_tasks = []

        # VLLM test tasks
        if self.config.get("VLLM") is not None:
            for vllm_name, config in self.config.get("VLLM", {}).items():
                if "api_key" in config and any(
                    x in config["api_key"] for x in ["Your", "placeholder", "sk-xxx"]
                ):
                    print(f"⏭️  VLLM {vllm_name} not configured with api_key, skipped")
                    continue
                print(f"🖼️ AddVLLMTest Task: {vllm_name}")
                all_tasks.append(self._test_vllm(vllm_name, config))

        print(f"\n✅ Find {len(all_tasks)} available visual large models")
        print(f"✅ Use {len(self.test_images)} test images")
        print(f"✅ Use {len(self.test_questions)} test questions")
        print("\n⏳ Start concurrent testing of all models...\n")

        # Run all test tasks concurrently
        all_results = await asyncio.gather(*all_tasks, return_exceptions=True)

        # Processing Result
        for result in all_results:
            if isinstance(result, dict) and result["errors"] == 0:
                self.results["vllm"][result["name"]] = result

        # Print Result
        print("\n📊 Generate test report...")
        self._print_results()


async def main():
    tester = AsyncVisionPerformanceTester()
    await tester.run()


if __name__ == "__main__":
    asyncio.run(main())
