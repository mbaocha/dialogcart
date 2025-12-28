#!/usr/bin/env python3
"""
Performance profiling script for Luma pipeline.

Profiles the entire pipeline execution and identifies bottlenecks.
Uses cProfile for detailed function-level profiling and memory_profiler for memory analysis.

Usage:
    python -m luma.perf.profile_luma
    python -m luma.perf.profile_luma --scenarios 50 --output profile_results.txt
"""
import sys
from pathlib import Path

# Add src/ to path if running directly
if __name__ == "__main__":
    src_path = Path(__file__).parent.parent.parent
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

import cProfile
import pstats
import io
import time
import argparse
from typing import Dict, Any, List
from datetime import datetime
from collections import defaultdict

from luma.pipeline import LumaPipeline
from luma.tests.followup_scenarios import followup_scenarios
from luma.tests.booking_scenarios import booking_scenarios
from luma.tests.other_scenarios import other_scenarios


def run_pipeline_with_timing(
    pipeline: LumaPipeline,
    text: str,
    tenant_context: Dict[str, Any] = None,
    iterations: int = 1
) -> Dict[str, Any]:
    """Run pipeline and collect timing data."""
    now = datetime.now()
    timings = []
    total_times = defaultdict(float)
    
    for _ in range(iterations):
        start = time.perf_counter()
        result = pipeline.run(
            text=text,
            now=now,
            timezone="UTC",
            tenant_context=tenant_context,
            booking_mode=tenant_context.get("booking_mode") if tenant_context else None
        )
        elapsed = (time.perf_counter() - start) * 1000  # Convert to ms
        
        timings.append(elapsed)
        
        # Collect stage timings
        stage_timings = result.get("execution_trace", {}).get("timings", {})
        for stage, duration in stage_timings.items():
            total_times[stage] += duration
    
    # Calculate averages
    avg_total = sum(timings) / len(timings)
    avg_stages = {stage: total / iterations for stage, total in total_times.items()}
    
    return {
        "total_ms": avg_total,
        "stages": avg_stages,
        "iterations": iterations,
        "min_ms": min(timings),
        "max_ms": max(timings),
        "text": text
    }


def profile_pipeline(
    pipeline: LumaPipeline,
    text: str,
    tenant_context: Dict[str, Any] = None
) -> pstats.Stats:
    """Profile a single pipeline execution using cProfile."""
    profiler = cProfile.Profile()
    
    now = datetime.now()
    profiler.enable()
    result = pipeline.run(
        text=text,
        now=now,
        timezone="UTC",
        tenant_context=tenant_context,
        booking_mode=tenant_context.get("booking_mode") if tenant_context else None
    )
    profiler.disable()
    
    return profiler, result


def analyze_profile_stats(stats: pstats.Stats, top_n: int = 30) -> Dict[str, Any]:
    """Analyze profile statistics and extract key metrics."""
    # Capture stats output
    stream = io.StringIO()
    stats.sort_stats('cumulative')
    # Redirect stdout to capture print_stats output
    import sys
    old_stdout = sys.stdout
    sys.stdout = stream
    try:
        stats.print_stats(top_n)
    finally:
        sys.stdout = old_stdout
    stats_output = stream.getvalue()
    
    # Extract function-level data
    functions = []
    for func_name, (cc, nc, tt, ct, callers) in stats.stats.items():
        functions.append({
            "name": func_name[2] if len(func_name) > 2 else str(func_name),
            "file": func_name[0],
            "line": func_name[1],
            "cumulative_time": ct,
            "total_time": tt,
            "call_count": cc,
            "ncalls": nc
        })
    
    # Sort by cumulative time
    functions.sort(key=lambda x: x["cumulative_time"], reverse=True)
    
    return {
        "top_functions": functions[:top_n],
        "stats_output": stats_output,
        "total_functions": len(functions)
    }


def identify_bottlenecks(timing_data: List[Dict[str, Any]], profile_data: List[Dict[str, Any]] = None) -> tuple:
    """Identify performance bottlenecks from profiling data."""
    bottlenecks = {
        "slow_stages": [],
        "slow_functions": [],
        "frequent_calls": [],
        "memory_hotspots": []
    }
    
    # Aggregate stage timings from timing_data
    stage_totals = defaultdict(float)
    stage_counts = defaultdict(int)
    
    for data in timing_data:
        stages = data.get("stages", {})
        for stage, duration in stages.items():
            stage_totals[stage] += duration
            stage_counts[stage] += 1
    
    # Calculate average stage times
    avg_stages = {
        stage: stage_totals[stage] / stage_counts[stage]
        for stage in stage_totals
    }
    
    # Identify slow stages (>100ms average)
    for stage, avg_time in sorted(avg_stages.items(), key=lambda x: x[1], reverse=True):
        if avg_time > 100:
            bottlenecks["slow_stages"].append({
                "stage": stage,
                "avg_ms": round(avg_time, 2),
                "count": stage_counts[stage]
            })
    
    # Aggregate function-level data
    all_functions = []
    if profile_data:
        for data in profile_data:
            functions = data.get("profile_analysis", {}).get("top_functions", [])
            all_functions.extend(functions)
    
    # Group by function name and aggregate
    func_aggregates = defaultdict(lambda: {"total_time": 0, "call_count": 0, "cumulative_time": 0})
    for func in all_functions:
        name = func["name"]
        func_aggregates[name]["total_time"] += func["total_time"]
        func_aggregates[name]["cumulative_time"] += func["cumulative_time"]
        func_aggregates[name]["call_count"] += func["call_count"]
    
    # Identify slow functions
    for name, agg in sorted(func_aggregates.items(), key=lambda x: x[1]["cumulative_time"], reverse=True)[:20]:
        if agg["cumulative_time"] > 0.01:  # More than 10ms cumulative
            bottlenecks["slow_functions"].append({
                "function": name,
                "cumulative_time_ms": round(agg["cumulative_time"] * 1000, 2),
                "total_time_ms": round(agg["total_time"] * 1000, 2),
                "call_count": agg["call_count"]
            })
    
    # Identify frequently called functions
    for name, agg in sorted(func_aggregates.items(), key=lambda x: x[1]["call_count"], reverse=True)[:20]:
        if agg["call_count"] > 100:
            bottlenecks["frequent_calls"].append({
                "function": name,
                "call_count": agg["call_count"],
                "avg_time_per_call_ms": round((agg["total_time"] * 1000) / agg["call_count"], 4)
            })
    
    return bottlenecks, avg_stages


def generate_report(
    timing_data: List[Dict[str, Any]],
    bottlenecks: Dict[str, Any],
    avg_stages: Dict[str, float],
    output_file: str = None
) -> str:
    """Generate a comprehensive performance report."""
    report_lines = []
    report_lines.append("=" * 80)
    report_lines.append("LUMA PIPELINE PERFORMANCE PROFILE REPORT")
    report_lines.append("=" * 80)
    report_lines.append("")
    
    # Summary statistics
    total_times = [d["total_ms"] for d in timing_data]
    avg_total = sum(total_times) / len(total_times)
    min_total = min(total_times)
    max_total = max(total_times)
    
    report_lines.append("SUMMARY STATISTICS")
    report_lines.append("-" * 80)
    report_lines.append(f"Total scenarios profiled: {len(timing_data)}")
    report_lines.append(f"Average total time: {avg_total:.2f} ms")
    report_lines.append(f"Min total time: {min_total:.2f} ms")
    report_lines.append(f"Max total time: {max_total:.2f} ms")
    report_lines.append("")
    
    # Stage timings
    report_lines.append("STAGE TIMINGS (Average)")
    report_lines.append("-" * 80)
    for stage, avg_time in sorted(avg_stages.items(), key=lambda x: x[1], reverse=True):
        percentage = (avg_time / avg_total * 100) if avg_total > 0 else 0
        report_lines.append(f"  {stage:20s}: {avg_time:8.2f} ms ({percentage:5.1f}%)")
    report_lines.append("")
    
    # Bottlenecks
    report_lines.append("PERFORMANCE BOTTLENECKS")
    report_lines.append("-" * 80)
    
    if bottlenecks["slow_stages"]:
        report_lines.append("\nSlow Stages (>100ms average):")
        for stage_info in bottlenecks["slow_stages"]:
            report_lines.append(
                f"  - {stage_info['stage']:20s}: {stage_info['avg_ms']:8.2f} ms "
                f"(seen {stage_info['count']} times)"
            )
    
    if bottlenecks["slow_functions"]:
        report_lines.append("\nSlow Functions (Top 10 by cumulative time):")
        for func_info in bottlenecks["slow_functions"][:10]:
            report_lines.append(
                f"  - {func_info['function']:50s}: "
                f"{func_info['cumulative_time_ms']:8.2f} ms cumulative, "
                f"{func_info['call_count']} calls"
            )
    
    if bottlenecks["frequent_calls"]:
        report_lines.append("\nFrequently Called Functions (>100 calls):")
        for func_info in bottlenecks["frequent_calls"][:10]:
            report_lines.append(
                f"  - {func_info['function']:50s}: "
                f"{func_info['call_count']:6d} calls, "
                f"{func_info['avg_time_per_call_ms']:6.4f} ms/call"
            )
    
    report_lines.append("")
    
    # Recommendations
    report_lines.append("RECOMMENDATIONS")
    report_lines.append("-" * 80)
    
    recommendations = []
    
    # Check for slow extraction stage
    if "extraction" in avg_stages and avg_stages["extraction"] > 200:
        recommendations.append(
            "• Extraction stage is slow (>200ms). Consider:\n"
            "  - Caching spaCy model initialization\n"
            "  - Optimizing entity pattern matching\n"
            "  - Reducing fuzzy matching overhead"
        )
    
    # Check for slow semantic stage
    if "semantic" in avg_stages and avg_stages["semantic"] > 300:
        recommendations.append(
            "• Semantic resolution is slow (>300ms). Consider:\n"
            "  - Caching semantic resolution results\n"
            "  - Optimizing date/time parsing logic\n"
            "  - Reducing redundant computations"
        )
    
    # Check for slow calendar binding
    if "binder" in avg_stages and avg_stages["binder"] > 150:
        recommendations.append(
            "• Calendar binding is slow (>150ms). Consider:\n"
            "  - Caching timezone conversions\n"
            "  - Optimizing date arithmetic\n"
            "  - Reducing validation overhead"
        )
    
    # Check for frequent function calls
    if bottlenecks["frequent_calls"]:
        top_frequent = bottlenecks["frequent_calls"][0]
        if top_frequent["call_count"] > 1000:
            recommendations.append(
                f"• {top_frequent['function']} is called very frequently "
                f"({top_frequent['call_count']} times). Consider:\n"
                "  - Adding memoization/caching\n"
                "  - Reducing call frequency through refactoring"
            )
    
    if not recommendations:
        recommendations.append("• No major bottlenecks identified. Performance looks good!")
    
    for rec in recommendations:
        report_lines.append(rec)
    
    report_lines.append("")
    report_lines.append("=" * 80)
    
    report_text = "\n".join(report_lines)
    
    if output_file:
        with open(output_file, "w") as f:
            f.write(report_text)
        print(f"Report written to {output_file}")
    
    return report_text


def main():
    """Main profiling entry point."""
    parser = argparse.ArgumentParser(description="Profile Luma pipeline performance")
    parser.add_argument(
        "--scenarios",
        type=int,
        default=20,
        help="Number of scenarios to profile (default: 20)"
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Number of iterations per scenario for averaging (default: 1)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file for report (default: print to stdout)"
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Enable detailed cProfile analysis (slower but more detailed)"
    )
    args = parser.parse_args()
    
    print("Initializing Luma pipeline...")
    pipeline = LumaPipeline(domain="service")
    
    # Collect test scenarios
    all_scenarios = []
    
    # Add followup scenarios
    for scenario in followup_scenarios[:args.scenarios]:
        for turn in scenario.get("turns", []):
            all_scenarios.append({
                "text": turn["sentence"],
                "tenant_context": {
                    "aliases": scenario.get("aliases", {}),
                    "booking_mode": scenario.get("booking_mode", "service")
                }
            })
    
    # Add booking scenarios if needed
    if len(all_scenarios) < args.scenarios:
        for scenario in booking_scenarios[:args.scenarios - len(all_scenarios)]:
            all_scenarios.append({
                "text": scenario.get("sentence", ""),
                "tenant_context": scenario.get("tenant_context", {})
            })
    
    # Add other scenarios if needed
    if len(all_scenarios) < args.scenarios:
        for scenario in other_scenarios[:args.scenarios - len(all_scenarios)]:
            all_scenarios.append({
                "text": scenario.get("sentence", ""),
                "tenant_context": scenario.get("tenant_context", {})
            })
    
    # Limit to requested number
    all_scenarios = all_scenarios[:args.scenarios]
    
    print(f"Profiling {len(all_scenarios)} scenarios...")
    
    timing_data = []
    profile_data = []
    
    for i, scenario in enumerate(all_scenarios, 1):
        text = scenario["text"]
        tenant_context = scenario.get("tenant_context")
        
        print(f"  [{i}/{len(all_scenarios)}] {text[:50]}...")
        
        # Run with timing
        timing_result = run_pipeline_with_timing(
            pipeline,
            text,
            tenant_context,
            iterations=args.iterations
        )
        timing_data.append(timing_result)
        
        # Run with profiling if requested
        if args.profile:
            profiler, result = profile_pipeline(pipeline, text, tenant_context)
            stats = pstats.Stats(profiler)
            analysis = analyze_profile_stats(stats)
            timing_result["profile_analysis"] = analysis
            profile_data.append(timing_result)
    
    print("\nAnalyzing results...")
    
    # Identify bottlenecks
    bottlenecks, avg_stages = identify_bottlenecks(timing_data, profile_data if args.profile else [])
    
    # Generate report
    report = generate_report(timing_data, bottlenecks, avg_stages, args.output)
    
    if not args.output:
        print("\n" + report)
    else:
        print(f"\nProfile complete! Report saved to {args.output}")


if __name__ == "__main__":
    main()

