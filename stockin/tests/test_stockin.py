import json
import requests
import time
from datetime import datetime
from typing import List, Dict, Tuple
from collections import defaultdict

API_BASE_URL = "http://localhost:8000"
INCOMING_FILE = "data/incoming.json"
DELAY_BETWEEN_REQUESTS = 0.1

class PerformanceTester:
    def __init__(self, api_url: str, incoming_file: str):
        self.api_url = api_url
        self.incoming_file = incoming_file
        self.endpoint_stats = defaultdict(lambda: {
            "calls": 0,
            "total_time": 0,
            "times": [],
            "min_time": float('inf'),
            "max_time": 0
        })
        self.results = {
            "success": [],
            "failed": [],
            "start_time": None,
            "end_time": None
        }
    
    def load_boxes(self) -> List[Dict]:
        with open(self.incoming_file, 'r') as f:
            return json.load(f)
    
    def check_api_health(self) -> bool:
        try:
            response = requests.get(f"{self.api_url}/health", timeout=5)
            return response.status_code == 200
        except:
            return False
    
    def track_request(self, endpoint: str, func, *args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        duration = (time.time() - start) * 1000
        
        stats = self.endpoint_stats[endpoint]
        stats["calls"] += 1
        stats["total_time"] += duration
        stats["times"].append(duration)
        stats["min_time"] = min(stats["min_time"], duration)
        stats["max_time"] = max(stats["max_time"], duration)
        
        return result, duration
    
    def suggest_placement(self, box_data: Dict) -> Tuple[int, Dict, float]:
        endpoint = f"{self.api_url}/stockin/suggest"
        
        payload = {
            "Barcode": box_data["Barcode"],
            "Batch": box_data["Batch"],
            "Exp": box_data["Exp"],
            "Width": box_data["Width"],
            "Height": box_data["Height"],
            "Depth": box_data["Depth"],
            "Weight": box_data.get("Weight", 0),
            "DeliveryId": box_data.get("DeliveryId", None)
        }
        
        def make_request():
            try:
                response = requests.post(endpoint, json=payload, timeout=30)
                return response.status_code, response.json()
            except Exception as e:
                return None, {"error": str(e)}
        
        (status, response), duration = self.track_request("/stockin/suggest", make_request)
        return status, response, duration
    
    def complete_task(self, task_id: str) -> Tuple[int, Dict, float]:
        endpoint = f"{self.api_url}/task/{task_id}/complete"
        
        def make_request():
            try:
                response = requests.post(endpoint, timeout=30)
                return response.status_code, response.json()
            except Exception as e:
                return None, {"error": str(e)}
        
        (status, response), duration = self.track_request(f"/task/complete", make_request)
        return status, response, duration
    
    def get_performance_stats(self) -> Dict:
        try:
            response = requests.get(f"{self.api_url}/performance/stats", timeout=5)
            if response.status_code == 200:
                return response.json()
        except:
            pass
        return {}
    
    def reset_performance_stats(self):
        try:
            requests.post(f"{self.api_url}/performance/reset", timeout=5)
        except:
            pass
    
    def print_progress_bar(self, current: int, total: int, bar_length: int = 50):
        percent = current / total
        filled = int(bar_length * percent)
        bar = '█' * filled + '░' * (bar_length - filled)
        print(f"\r  [{bar}] {current}/{total} ({percent*100:.1f}%)", end='', flush=True)
    
    def run(self):
        print("=" * 100)
        print(" " * 30 + "PERFORMANCE TESTING - 50 BOX PLACEMENT")
        print("=" * 100)
        
        print(f"\nChecking API health at {self.api_url}...")
        if not self.check_api_health():
            print("ERROR: API is not responding. Please start your FastAPI server.")
            return
        print("API is healthy\n")
        
        print("Resetting API performance counters...")
        self.reset_performance_stats()
        print("Performance counters reset\n")
        
        print(f"Loading boxes from {self.incoming_file}...")
        try:
            boxes = self.load_boxes()
        except FileNotFoundError:
            print(f"ERROR: File '{self.incoming_file}' not found!")
            return
        print(f"Loaded {len(boxes)} boxes\n")
        
        print("Starting placement process...")
        print("-" * 100)
        
        self.results["start_time"] = datetime.now()
        total_suggest_time = 0
        total_complete_time = 0
        
        for idx, box in enumerate(boxes, 1):
            self.print_progress_bar(idx, len(boxes))
            
            status_code, response, suggest_time = self.suggest_placement(box)
            total_suggest_time += suggest_time
            
            if status_code == 200:
                task_id = response.get("task_id")
                
                time.sleep(0.05)
                complete_status, complete_response, complete_time = self.complete_task(task_id)
                total_complete_time += complete_time
                
                if complete_status == 200:
                    self.results["success"].append({
                        "index": idx,
                        "barcode": box["Barcode"],
                        "dimensions": f"{box['Width']}×{box['Height']}×{box['Depth']}mm",
                        "task_id": task_id,
                        "vsu": response.get("placement", {}).get("vsu_code"),
                        "is_new_vsu": response.get("is_new_vsu"),
                        "suggest_time_ms": suggest_time,
                        "complete_time_ms": complete_time,
                        "total_time_ms": suggest_time + complete_time
                    })
                else:
                    self.results["failed"].append({
                        "index": idx,
                        "barcode": box["Barcode"],
                        "dimensions": f"{box['Width']}×{box['Height']}×{box['Depth']}mm",
                        "stage": "completion",
                        "error": "Task completion failed",
                        "details": complete_response
                    })
            elif status_code == 400:
                # Warehouse full or item doesn't fit
                error_detail = response.get("detail", {})
                self.results["failed"].append({
                    "index": idx,
                    "barcode": box["Barcode"],
                    "dimensions": f"{box['Width']}×{box['Height']}×{box['Depth']}mm",
                    "stage": "placement",
                    "error": "No suitable location",
                    "reason": error_detail.get("reason", "Unknown") if isinstance(error_detail, dict) else str(error_detail),
                    "details": error_detail
                })
            else:
                # Other errors
                self.results["failed"].append({
                    "index": idx,
                    "barcode": box["Barcode"],
                    "dimensions": f"{box['Width']}×{box['Height']}×{box['Depth']}mm",
                    "stage": "placement",
                    "error": f"HTTP {status_code}" if status_code else "Connection error",
                    "details": response
                })
            
            time.sleep(DELAY_BETWEEN_REQUESTS)
        
        self.results["end_time"] = datetime.now()
        print("\n")
        
        api_stats = self.get_performance_stats()
        self.print_detailed_statistics(api_stats)
        self.save_results()
    
    def print_detailed_statistics(self, api_stats: Dict):
        duration = (self.results["end_time"] - self.results["start_time"]).total_seconds()
        total_boxes = len(self.results["success"]) + len(self.results["failed"])
        
        print("=" * 100)
        print(" " * 35 + "PERFORMANCE STATISTICS")
        print("=" * 100)
        
        print("\nOVERALL SUMMARY")
        print("-" * 100)
        print(f"Total Duration:           {duration:.2f} seconds")
        print(f"Total Boxes:              {total_boxes}")
        print(f"Successfully Placed:      {len(self.results['success'])}")
        print(f"Failed:                   {len(self.results['failed'])}")
        print(f"Average Time per Box:     {(duration/total_boxes*1000):.2f} ms")
        print(f"Throughput:               {(total_boxes/duration):.2f} boxes/second")
        
        # Add failed boxes section if there are failures
        if self.results["failed"]:
            print("\nFAILED PLACEMENTS - DETAILED BREAKDOWN")
            print("=" * 100)
            print(f"Total Failed: {len(self.results['failed'])}")
            print("-" * 100)
            print(f"{'#':<5} {'Barcode':<20} {'Dimensions':<20} {'Stage':<12} {'Reason':<30}")
            print("-" * 100)
            
            for failed in self.results["failed"]:
                reason = failed.get("reason", failed.get("error", "Unknown"))
                if len(reason) > 28:
                    reason = reason[:28] + "..."
                print(f"{failed['index']:<5} {failed['barcode']:<20} {failed['dimensions']:<20} "
                      f"{failed.get('stage', 'unknown'):<12} {reason:<30}")
            
            print("\nFAILURE ANALYSIS")
            print("-" * 100)
            
            # Group failures by reason
            failure_reasons = defaultdict(list)
            for failed in self.results["failed"]:
                reason = failed.get("reason", failed.get("error", "Unknown"))
                failure_reasons[reason].append(failed)
            
            print(f"{'Failure Reason':<50} {'Count':<10} {'%':<10}")
            print("-" * 100)
            for reason, items in sorted(failure_reasons.items(), key=lambda x: len(x[1]), reverse=True):
                count = len(items)
                percent = (count / len(self.results["failed"]) * 100)
                reason_short = reason[:48] + "..." if len(reason) > 48 else reason
                print(f"{reason_short:<50} {count:<10} {percent:<10.1f}%")
            
            # Show dimensions of failed boxes
            print("\nFAILED BOX DIMENSIONS")
            print("-" * 100)
            failed_dims = [f.get("dimensions", "N/A") for f in self.results["failed"]]
            from collections import Counter
            dim_counts = Counter(failed_dims)
            print(f"{'Dimensions (W×H×D)':<25} {'Count':<10}")
            print("-" * 100)
            for dims, count in dim_counts.most_common():
                print(f"{dims:<25} {count:<10}")
        
        print("\n⏱️  CLIENT-SIDE ENDPOINT TIMING (Measured by Test Script)")
        print("-" * 100)
        print(f"{'Endpoint':<30} {'Calls':<8} {'Total(ms)':<12} {'Avg(ms)':<10} {'Min(ms)':<10} {'Max(ms)':<10}")
        print("-" * 100)
        
        for endpoint, stats in sorted(self.endpoint_stats.items()):
            avg_time = stats["total_time"] / stats["calls"] if stats["calls"] > 0 else 0
            print(f"{endpoint:<30} {stats['calls']:<8} {stats['total_time']:<12.2f} "
                  f"{avg_time:<10.2f} {stats['min_time']:<10.2f} {stats['max_time']:<10.2f}")
        
        if api_stats and "endpoints" in api_stats:
            print("\n🖥️  SERVER-SIDE ENDPOINT TIMING (Measured by API)")
            print("-" * 100)
            print(f"{'Endpoint':<35} {'Calls':<8} {'Avg(ms)':<10} {'Min(ms)':<10} {'Max(ms)':<10}")
            print("-" * 100)
            
            for endpoint_data in api_stats["endpoints"][:10]:
                endpoint = endpoint_data["endpoint"]
                times = endpoint_data["times_ms"]
                calls = endpoint_data["calls"]
                print(f"{endpoint:<35} {calls:<8} {times['avg']:<10.2f} "
                      f"{times['min']:<10.2f} {times['max']:<10.2f}")
        
        if self.results["success"]:
            print("\n📦 PER-BOX TIMING BREAKDOWN")
            print("-" * 100)
            
            suggest_times = [r["suggest_time_ms"] for r in self.results["success"]]
            complete_times = [r["complete_time_ms"] for r in self.results["success"]]
            total_times = [r["total_time_ms"] for r in self.results["success"]]
            
            print(f"{'Operation':<20} {'Total(ms)':<12} {'Avg(ms)':<10} {'Min(ms)':<10} {'Max(ms)':<10}")
            print("-" * 100)
            print(f"{'Suggest Placement':<20} {sum(suggest_times):<12.2f} "
                  f"{(sum(suggest_times)/len(suggest_times)):<10.2f} "
                  f"{min(suggest_times):<10.2f} {max(suggest_times):<10.2f}")
            print(f"{'Complete Task':<20} {sum(complete_times):<12.2f} "
                  f"{(sum(complete_times)/len(complete_times)):<10.2f} "
                  f"{min(complete_times):<10.2f} {max(complete_times):<10.2f}")
            print(f"{'Total per Box':<20} {sum(total_times):<12.2f} "
                  f"{(sum(total_times)/len(total_times)):<10.2f} "
                  f"{min(total_times):<10.2f} {max(total_times):<10.2f}")
        
        if self.results["success"]:
            print("\n🐌 TOP 10 SLOWEST BOXES")
            print("-" * 100)
            print(f"{'#':<5} {'Barcode':<20} {'Total(ms)':<12} {'Suggest(ms)':<12} {'Complete(ms)':<12}")
            print("-" * 100)
            
            slowest = sorted(self.results["success"], key=lambda x: x["total_time_ms"], reverse=True)[:10]
            for item in slowest:
                print(f"{item['index']:<5} {item['barcode']:<20} {item['total_time_ms']:<12.2f} "
                      f"{item['suggest_time_ms']:<12.2f} {item['complete_time_ms']:<12.2f}")
        
        if self.results["success"]:
            print("\n⚡ TOP 10 FASTEST BOXES")
            print("-" * 100)
            print(f"{'#':<5} {'Barcode':<20} {'Total(ms)':<12} {'Suggest(ms)':<12} {'Complete(ms)':<12}")
            print("-" * 100)
            
            fastest = sorted(self.results["success"], key=lambda x: x["total_time_ms"])[:10]
            for item in fastest:
                print(f"{item['index']:<5} {item['barcode']:<20} {item['total_time_ms']:<12.2f} "
                      f"{item['suggest_time_ms']:<12.2f} {item['complete_time_ms']:<12.2f}")
        
        if self.results["success"]:
            print("\n📈 TIME DISTRIBUTION")
            print("-" * 100)
            
            total_times = [r["total_time_ms"] for r in self.results["success"]]
            ranges = [
                ("< 100ms", lambda t: t < 100),
                ("100-200ms", lambda t: 100 <= t < 200),
                ("200-300ms", lambda t: 200 <= t < 300),
                ("300-500ms", lambda t: 300 <= t < 500),
                ("> 500ms", lambda t: t >= 500)
            ]
            
            for range_name, condition in ranges:
                count = sum(1 for t in total_times if condition(t))
                percent = (count / len(total_times) * 100) if total_times else 0
                bar = '█' * int(percent / 2)
                print(f"{range_name:<12} {count:>3} boxes  {bar:<50} {percent:.1f}%")
        
        print("\n" + "=" * 100)
    
    def save_results(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_file = f"performance_test_{timestamp}.json"
        
        endpoint_summary = {}
        for endpoint, stats in self.endpoint_stats.items():
            avg_time = stats["total_time"] / stats["calls"] if stats["calls"] > 0 else 0
            endpoint_summary[endpoint] = {
                "calls": stats["calls"],
                "total_time_ms": round(stats["total_time"], 2),
                "avg_time_ms": round(avg_time, 2),
                "min_time_ms": round(stats["min_time"], 2),
                "max_time_ms": round(stats["max_time"], 2),
                "all_times_ms": [round(t, 2) for t in stats["times"]]
            }
        
        save_data = {
            "test_info": {
                "start_time": self.results["start_time"].isoformat(),
                "end_time": self.results["end_time"].isoformat(),
                "duration_seconds": (self.results["end_time"] - self.results["start_time"]).total_seconds(),
                "total_boxes": len(self.results["success"]) + len(self.results["failed"]),
                "successful": len(self.results["success"]),
                "failed": len(self.results["failed"])
            },
            "endpoint_statistics": endpoint_summary,
            "per_box_results": self.results["success"],
            "failed_boxes": self.results["failed"]
        }
        
        with open(results_file, 'w') as f:
            json.dump(save_data, f, indent=2)
        
        print(f"\nDetailed results saved to: {results_file}")
        print(f"\nTo view API's internal stats: curl {self.api_url}/performance/stats")
        print(f"To view slowest endpoints: curl {self.api_url}/performance/slowest")

def main():
    print("\nMedicPort Performance Testing - Detailed Endpoint Analysis")
    print("=" * 100)
    
    tester = PerformanceTester(API_BASE_URL, INCOMING_FILE)
    tester.run()
    
    print("\nPerformance testing completed!")
    print("\nNext steps:")
    print("  - Review the detailed statistics above")
    print("  - Check performance_test_*.json for raw data")
    print("  - Identify slow endpoints and optimize if needed")
    print("  - Compare with future test runs to track improvements\n")

if __name__ == "__main__":
    main()
