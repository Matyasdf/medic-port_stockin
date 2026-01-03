"""
Performance Test Script - Dispense 100 Random Boxes
Measures timing and success rates for dispensing operations
"""

import requests
import random
import time
import json
from datetime import datetime
from collections import defaultdict

BASE_URL = "http://localhost:8000"

def get_available_barcodes():
    """Get list of barcodes with available stock from ml_robot_updated.json"""
    try:
        with open("data/ml_robot_updated.json", "r") as f:
            data = json.load(f)

        # Count items by barcode from ItemPlacements
        barcode_counts = defaultdict(lambda: {"quantity": 0, "product_id": None})

        for placement in data.get("ItemPlacements", []):
            metadata = placement.get("ItemMetadata", {})
            barcode = metadata.get("Barcode")
            product_id = metadata.get("ProductID")

            if barcode:
                barcode_counts[barcode]["quantity"] += 1
                barcode_counts[barcode]["product_id"] = product_id

        available = []
        for barcode, info in barcode_counts.items():
            if info["quantity"] > 0:
                available.append({
                    "barcode": barcode,
                    "product_id": info["product_id"],
                    "quantity": info["quantity"]
                })

        return available
    except Exception as e:
        print(f"Error reading inventory: {e}")
        return []

def dispense_single(barcode: str, output_id: int = 1):
    """Dispense a single item and return timing info"""
    start_time = time.time()

    payload = {
        "products": [{"barcode": barcode, "quantity": 1}],
        "output_id": output_id
    }

    # Create dispense request
    create_start = time.time()
    response = requests.post(f"{BASE_URL}/dispense/create", json=payload)
    create_time = time.time() - create_start

    if response.status_code != 200:
        return {
            "success": False,
            "error": f"Create failed: {response.status_code} - {response.text[:200]}",
            "barcode": barcode,
            "total_time": time.time() - start_time
        }

    result = response.json()
    task_id = result.get("task_id")

    if not task_id:
        return {
            "success": False,
            "error": "No task_id in response",
            "barcode": barcode,
            "total_time": time.time() - start_time
        }

    # Check for obstructions and complete relocations first
    has_obstruction = False
    relocate_time = 0

    for inst in result.get("instructions", []):
        for item in inst.get("items", []):
            if item.get("action") == "relocate":
                has_obstruction = True
                relocate_start = time.time()
                relocate_task_id = item.get("relocate_task_id")
                if relocate_task_id:
                    # Call relocate endpoint with relocate_task_id
                    relocate_resp = requests.post(
                        f"{BASE_URL}/api/temporary/relocate",
                        json={"task_id": relocate_task_id}
                    )
                    if relocate_resp.status_code != 200:
                        print(f"    Relocate warning: {relocate_resp.status_code}")
                relocate_time += time.time() - relocate_start

    # Complete the entire dispense task with task_id
    complete_start = time.time()
    complete_payload = {"task_id": task_id}
    complete_resp = requests.post(f"{BASE_URL}/dispense/complete", json=complete_payload)
    complete_time = time.time() - complete_start

    if complete_resp.status_code != 200:
        return {
            "success": False,
            "error": f"Complete failed: {complete_resp.status_code} - {complete_resp.text[:200]}",
            "barcode": barcode,
            "task_id": task_id,
            "total_time": time.time() - start_time
        }

    complete_result = complete_resp.json()
    dispense_count = complete_result.get("items_dispensed", 1)

    total_time = time.time() - start_time

    return {
        "success": True,
        "barcode": barcode,
        "task_id": task_id,
        "create_time": create_time,
        "complete_time": complete_time,
        "relocate_time": relocate_time,
        "total_time": total_time,
        "has_obstruction": has_obstruction,
        "items_dispensed": dispense_count
    }

def run_performance_test(num_dispenses: int = 100):
    """Run performance test with random dispenses"""
    print("=" * 70)
    print(f"PERFORMANCE TEST - {num_dispenses} Random Dispenses")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # Reset robots before starting
    print("\nResetting robots...")
    try:
        reset_resp = requests.post(f"{BASE_URL}/robots/reset")
        if reset_resp.status_code == 200:
            print("Robots reset to IDLE")
        else:
            print(f"Warning: Robot reset failed: {reset_resp.status_code}")
    except Exception as e:
        print(f"Warning: Could not reset robots: {e}")

    # Get available barcodes
    print("\nFetching inventory...")
    available = get_available_barcodes()

    if not available:
        print("ERROR: No items available in inventory!")
        return

    print(f"Found {len(available)} unique products with stock")
    total_stock = sum(item["quantity"] for item in available)
    print(f"Total items in stock: {total_stock}")

    if total_stock < num_dispenses:
        print(f"WARNING: Only {total_stock} items available, adjusting test to {total_stock} dispenses")
        num_dispenses = total_stock

    # Track results
    results = []
    success_count = 0
    failure_count = 0
    obstruction_count = 0

    # Track timing
    create_times = []
    complete_times = []
    relocate_times = []
    total_times = []

    # Track by product
    product_stats = defaultdict(lambda: {"success": 0, "fail": 0, "total_time": 0})

    print(f"\nStarting {num_dispenses} dispense operations...")
    print("-" * 70)

    test_start = time.time()

    for i in range(num_dispenses):
        # Pick a random barcode from available stock
        if not available:
            print(f"\n[{i+1}/{num_dispenses}] No more stock available!")
            break

        item = random.choice(available)
        barcode = item["barcode"]

        # Decrement local count (actual inventory is managed by server)
        item["quantity"] -= 1
        if item["quantity"] <= 0:
            available.remove(item)

        # Run dispense
        result = dispense_single(barcode)
        results.append(result)

        if result["success"]:
            success_count += 1
            create_times.append(result["create_time"])
            complete_times.append(result["complete_time"])
            total_times.append(result["total_time"])

            if result["has_obstruction"]:
                obstruction_count += 1
                relocate_times.append(result["relocate_time"])

            product_stats[barcode]["success"] += 1
            product_stats[barcode]["total_time"] += result["total_time"]

            status = "OK"
            if result["has_obstruction"]:
                status = "OK (relocated)"
        else:
            failure_count += 1
            product_stats[barcode]["fail"] += 1
            status = f"FAIL: {result.get('error', 'Unknown')[:50]}"

        # Progress output every 10 items
        if (i + 1) % 10 == 0 or i == 0:
            elapsed = time.time() - test_start
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            print(f"[{i+1:3d}/{num_dispenses}] {barcode[:20]:20s} - {status[:30]:30s} ({result['total_time']:.3f}s) | Rate: {rate:.1f}/s")

    test_duration = time.time() - test_start

    # Print summary
    print("\n" + "=" * 70)
    print("PERFORMANCE TEST RESULTS")
    print("=" * 70)

    print(f"\n--- Overall Statistics ---")
    print(f"Total dispenses attempted: {len(results)}")
    print(f"Successful: {success_count} ({100*success_count/len(results):.1f}%)")
    print(f"Failed: {failure_count} ({100*failure_count/len(results):.1f}%)")
    print(f"Obstructions encountered: {obstruction_count}")
    print(f"Total test duration: {test_duration:.2f}s")
    print(f"Average rate: {len(results)/test_duration:.2f} dispenses/second")

    if total_times:
        print(f"\n--- Timing Statistics ---")
        print(f"Create request time:")
        print(f"  Min: {min(create_times)*1000:.1f}ms | Max: {max(create_times)*1000:.1f}ms | Avg: {sum(create_times)/len(create_times)*1000:.1f}ms")
        print(f"Complete request time:")
        print(f"  Min: {min(complete_times)*1000:.1f}ms | Max: {max(complete_times)*1000:.1f}ms | Avg: {sum(complete_times)/len(complete_times)*1000:.1f}ms")
        print(f"Total dispense time (per item):")
        print(f"  Min: {min(total_times)*1000:.1f}ms | Max: {max(total_times)*1000:.1f}ms | Avg: {sum(total_times)/len(total_times)*1000:.1f}ms")

        if relocate_times:
            print(f"Relocate time (when needed):")
            print(f"  Min: {min(relocate_times)*1000:.1f}ms | Max: {max(relocate_times)*1000:.1f}ms | Avg: {sum(relocate_times)/len(relocate_times)*1000:.1f}ms")

    # Top products dispensed
    print(f"\n--- Top 10 Products Dispensed ---")
    sorted_products = sorted(product_stats.items(), key=lambda x: x[1]["success"], reverse=True)[:10]
    for barcode, stats in sorted_products:
        avg_time = stats["total_time"] / stats["success"] if stats["success"] > 0 else 0
        print(f"  {barcode[:25]:25s} | Dispensed: {stats['success']:3d} | Avg time: {avg_time*1000:.1f}ms")

    # Failures breakdown
    if failure_count > 0:
        print(f"\n--- Failure Details ---")
        failures = [r for r in results if not r["success"]]
        error_counts = defaultdict(int)
        for f in failures:
            error_counts[f.get("error", "Unknown")[:50]] += 1
        for error, count in sorted(error_counts.items(), key=lambda x: -x[1]):
            print(f"  {count}x: {error}")

    # Save results to file
    output_file = f"perf_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w') as f:
        json.dump({
            "test_config": {
                "num_dispenses": num_dispenses,
                "timestamp": datetime.now().isoformat()
            },
            "summary": {
                "success_count": success_count,
                "failure_count": failure_count,
                "obstruction_count": obstruction_count,
                "test_duration_seconds": test_duration,
                "dispenses_per_second": len(results) / test_duration
            },
            "timing": {
                "create_avg_ms": sum(create_times)/len(create_times)*1000 if create_times else 0,
                "complete_avg_ms": sum(complete_times)/len(complete_times)*1000 if complete_times else 0,
                "total_avg_ms": sum(total_times)/len(total_times)*1000 if total_times else 0
            },
            "results": results
        }, f, indent=2)

    print(f"\n--- Results saved to {output_file} ---")
    print("=" * 70)

    return results

if __name__ == "__main__":
    import sys

    num = 100
    if len(sys.argv) > 1:
        try:
            num = int(sys.argv[1])
        except:
            print(f"Usage: python {sys.argv[0]} [num_dispenses]")
            print(f"  Default: 100 dispenses")
            sys.exit(1)

    run_performance_test(num)
