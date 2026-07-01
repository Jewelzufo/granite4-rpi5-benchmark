#!/usr/bin/env python3
"""
Benchmark Analysis Script for Edge AI Model Evaluation

Analyzes JSON logs from ollama benchmark runs on Raspberry Pi 5,
parses evaluation markdown files, and generates comprehensive statistics.

Usage:
    python analyze_benchmarks.py [--logs-dir PATH] [--evals-dir PATH] [--output PATH]
"""

import json
import os
import re
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict
from collections import defaultdict
import statistics


@dataclass
class QueryMetrics:
    """Metrics for a single query."""
    tokens_per_second: float
    total_tokens: int
    generation_duration: float
    wall_clock_seconds: float
    cpu_temp_before: float
    cpu_temp_after: float
    throttled: bool


@dataclass
class RunStatistics:
    """Statistics for a single benchmark run."""
    run_id: str
    query_count: int
    avg_tokens_per_second: float
    std_tokens_per_second: float
    min_tokens_per_second: float
    max_tokens_per_second: float
    avg_generation_time: float
    avg_cpu_temp_rise: float
    throttling_events: int
    accuracy: Optional[float] = None
    pass_count: Optional[int] = None
    total_evaluated: Optional[int] = None


@dataclass
class CategoryStats:
    """Statistics for a question category."""
    category_name: str
    capability: str
    total_queries: int
    passed: int
    failed: int
    accuracy: float
    partial_credit_accuracy: float = 0.0


@dataclass
class BenchmarkSummary:
    """Overall benchmark summary."""
    analysis_timestamp: str
    total_runs: int
    total_queries: int
    total_evaluated: int
    overall_accuracy: float
    overall_accuracy_with_partial: float
    avg_throughput: float
    std_throughput: float
    min_throughput: float
    max_throughput: float
    avg_generation_time: float
    avg_cpu_temp_rise: float
    total_throttling_events: int
    hardware: str
    model: str
    quantization: str
    categories: List[Dict[str, Any]] = field(default_factory=list)
    run_statistics: List[Dict[str, Any]] = field(default_factory=list)


class BenchmarkAnalyzer:
    """Analyzes benchmark logs and evaluation files."""

    def __init__(self, logs_dir: str, evals_dir: str):
        self.logs_dir = Path(logs_dir)
        self.evals_dir = Path(evals_dir)
        self.runs_data: List[Dict[str, Any]] = []
        self.evaluations_data: List[Dict[str, Any]] = []
        self.category_stats: Dict[str, CategoryStats] = {}

    def load_json_logs(self) -> List[Dict[str, Any]]:
        """Load all JSON log files from the logs directory."""
        json_files = sorted(self.logs_dir.glob("*.json"))
        
        if not json_files:
            raise FileNotFoundError(f"No JSON files found in {self.logs_dir}")
        
        runs = []
        for json_file in json_files:
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    data['_filename'] = json_file.name
                    runs.append(data)
            except json.JSONDecodeError as e:
                print(f"Warning: Could not parse {json_file.name}: {e}")
            except Exception as e:
                print(f"Warning: Error reading {json_file.name}: {e}")
        
        self.runs_data = runs
        return runs

    def load_evaluation_markdowns(self) -> List[Dict[str, Any]]:
        """Parse evaluation markdown files to extract pass/fail results."""
        md_files = sorted(self.evals_dir.glob("*.md")) + sorted(self.evals_dir.glob("*.MD"))
        # Remove duplicates (case-insensitive)
        seen = set()
        unique_files = []
        for f in md_files:
            if f.stem.lower() not in seen:
                seen.add(f.stem.lower())
                unique_files.append(f)
        
        if not unique_files:
            print(f"Warning: No markdown evaluation files found in {self.evals_dir}")
            return []
        
        evaluations = []
        for md_file in unique_files:
            try:
                with open(md_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                parsed = self._parse_evaluation_md(content, md_file.name)
                if parsed:
                    evaluations.append(parsed)
            except Exception as e:
                print(f"Warning: Error parsing {md_file.name}: {e}")
        
        self.evaluations_data = evaluations
        return evaluations

    def _parse_evaluation_md(self, content: str, filename: str) -> Optional[Dict[str, Any]]:
        """Parse a single evaluation markdown file."""
        # Extract run number - use the numeric part from filename for consistency with JSON logs
        # e.g., "run1" -> "1", "RUN10" -> "10"
        run_match = re.search(r'RUN(\d+)', filename, re.IGNORECASE)
        if not run_match:
            # Try to extract from content as fallback
            run_match = re.search(r'###\s*Run\s+No\.\s*(\d+)', content, re.IGNORECASE)
        
        run_number = run_match.group(1) if run_match else filename
        
        # Extract metadata
        model_match = re.search(r'\*\*Model:\*\*\s*`([^`]+)`', content)
        quant_match = re.search(r'\*\*Quantization:\*\*\s*`([^`]+)`', content)
        evaluator_match = re.search(r'\*\*Evaluator:\*\*\s*`([^`]+)`', content)
        
        # Parse individual evaluations
        sections = re.split(r'---\n*', content)
        queries = []
        
        for section in sections:
            # Look for capability and pass/fail
            capability_match = re.search(r'\*\*Capability:\*\*\s*(.+?)(?:\n|$)', section)
            # Match various Pass/Fail formats: "**Pass/Fail:** PASS", "Pass/Fail:** PASS", etc.
            pass_match = re.search(r'\*?Pass/Fail[:\*]*\s*(PASS|FAIL|PARTIAL\s+PASS)', section, re.IGNORECASE)
            
            if capability_match and pass_match:
                capability = capability_match.group(1).strip()
                result = pass_match.group(1).upper()
                passed = result == 'PASS' or result == 'PARTIAL PASS'
                
                # Check for partial credit (explicit PARTIAL PASS or mentions of partial)
                is_partial = result == 'PARTIAL PASS' or (not passed and re.search(r'(partial|partially)', section, re.IGNORECASE))
                queries.append({
                    'capability': capability,
                    'passed': passed,
                    'partial_credit': is_partial and not (result == 'PASS')
                })
        
        if not queries:
            return None
        
        return {
            'run_id': run_number,
            'filename': filename,
            'model': model_match.group(1) if model_match else 'Unknown',
            'quantization': quant_match.group(1) if quant_match else 'Unknown',
            'evaluator': evaluator_match.group(1) if evaluator_match else 'Unknown',
            'queries': queries
        }

    def extract_query_metrics(self, run_data: Dict[str, Any]) -> List[QueryMetrics]:
        """Extract metrics from interactions in a run."""
        metrics = []
        interactions = run_data.get('interactions', [])
        
        for interaction in interactions:
            calc_metrics = interaction.get('calculated_metrics', {})
            hw_metrics = interaction.get('hardware_metrics', {})
            
            throttled_flags = hw_metrics.get('throttled_flags_after', {})
            throttled = any([
                throttled_flags.get('undervoltage_occurred', False),
                throttled_flags.get('arm_freq_capped', False),
                throttled_flags.get('throttled', False),
                throttled_flags.get('throttled_now', False)
            ])
            
            metric = QueryMetrics(
                tokens_per_second=calc_metrics.get('tokens_per_second', 0.0),
                total_tokens=calc_metrics.get('total_tokens_generated', 0),
                generation_duration=calc_metrics.get('generation_duration_seconds', 0.0),
                wall_clock_seconds=calc_metrics.get('total_wall_clock_seconds', 0.0),
                cpu_temp_before=hw_metrics.get('cpu_temp_celsius_before', 0.0),
                cpu_temp_after=hw_metrics.get('cpu_temp_celsius_after', 0.0),
                throttled=throttled
            )
            metrics.append(metric)
        
        return metrics

    def calculate_run_statistics(self, run_data: Dict[str, Any], 
                                  eval_data: Optional[Dict[str, Any]] = None) -> RunStatistics:
        """Calculate statistics for a single run."""
        metrics = self.extract_query_metrics(run_data)
        
        if not metrics:
            return RunStatistics(
                run_id=run_data.get('_filename', 'unknown'),
                query_count=0,
                avg_tokens_per_second=0.0,
                std_tokens_per_second=0.0,
                min_tokens_per_second=0.0,
                max_tokens_per_second=0.0,
                avg_generation_time=0.0,
                avg_cpu_temp_rise=0.0,
                throttling_events=0
            )
        
        tps_values = [m.tokens_per_second for m in metrics]
        gen_times = [m.generation_duration for m in metrics]
        temp_rises = [m.cpu_temp_after - m.cpu_temp_before for m in metrics]
        throttling_count = sum(1 for m in metrics if m.throttled)
        
        # Calculate accuracy from eval data if available
        accuracy = None
        pass_count = None
        total_evaluated = None
        
        if eval_data:
            queries = eval_data.get('queries', [])
            if queries:
                passed = sum(1 for q in queries if q['passed'])
                partial = sum(1 for q in queries if q.get('partial_credit', False))
                total_evaluated = len(queries)
                pass_count = passed
                accuracy = (passed + partial) / total_evaluated * 100 if total_evaluated > 0 else 0.0
        
        return RunStatistics(
            run_id=run_data.get('_filename', 'unknown').replace('.json', ''),
            query_count=len(metrics),
            avg_tokens_per_second=statistics.mean(tps_values),
            std_tokens_per_second=statistics.stdev(tps_values) if len(tps_values) > 1 else 0.0,
            min_tokens_per_second=min(tps_values),
            max_tokens_per_second=max(tps_values),
            avg_generation_time=statistics.mean(gen_times),
            avg_cpu_temp_rise=statistics.mean(temp_rises),
            throttling_events=throttling_count,
            accuracy=accuracy,
            pass_count=pass_count,
            total_evaluated=total_evaluated
        )

    def calculate_category_statistics(self) -> List[CategoryStats]:
        """Aggregate statistics by question category/capability."""
        category_data = defaultdict(lambda: {'total': 0, 'passed': 0, 'partial': 0})
        
        for eval_data in self.evaluations_data:
            for query in eval_data.get('queries', []):
                capability = query['capability']
                category_data[capability]['total'] += 1
                if query['passed']:
                    category_data[capability]['passed'] += 1
                elif query.get('partial_credit', False):
                    category_data[capability]['partial'] += 1
        
        stats = []
        for category, data in category_data.items():
            total = data['total']
            passed = data['passed']
            partial = data['partial']
            
            accuracy = (passed / total * 100) if total > 0 else 0.0
            partial_accuracy = ((passed + partial) / total * 100) if total > 0 else 0.0
            
            stats.append(CategoryStats(
                category_name=category,
                capability=category,
                total_queries=total,
                passed=passed,
                failed=total - passed - partial,
                accuracy=round(accuracy, 2),
                partial_credit_accuracy=round(partial_accuracy, 2)
            ))
        
        # Sort by accuracy descending
        stats.sort(key=lambda x: x.accuracy, reverse=True)
        self.category_stats = {s.category_name: s for s in stats}
        return stats

    def generate_summary(self) -> BenchmarkSummary:
        """Generate comprehensive benchmark summary."""
        if not self.runs_data:
            self.load_json_logs()
        if not self.evaluations_data:
            self.load_evaluation_markdowns()
        
        # Match runs with evaluations
        eval_map = {e['run_id']: e for e in self.evaluations_data}
        
        run_stats = []
        all_tps = []
        all_gen_times = []
        all_temp_rises = []
        total_throttling = 0
        total_queries = 0
        total_passed = 0
        total_evaluated = 0
        total_partial = 0
        
        for run_data in self.runs_data:
            run_id = run_data.get('_filename', '').replace('.json', '')
            # Extract numeric run ID from filename (e.g., "RUN10" -> "10", "run1" -> "1")
            run_num_match = re.search(r'RUN(\d+)', run_id, re.IGNORECASE)
            run_num = run_num_match.group(1) if run_num_match else run_id
            
            # Try multiple ID formats for matching
            eval_data = None
            for key in [run_num, run_id, run_id.upper(), run_id.lower()]:
                if key in eval_map:
                    eval_data = eval_map[key]
                    break
            
            stats = self.calculate_run_statistics(run_data, eval_data)
            run_stats.append(stats)
            
            # Aggregate metrics
            if stats.query_count > 0:
                all_tps.append(stats.avg_tokens_per_second)
                all_gen_times.append(stats.avg_generation_time)
                all_temp_rises.append(stats.avg_cpu_temp_rise)
                total_throttling += stats.throttling_events
                total_queries += stats.query_count
            
            # Aggregate evaluation data directly from eval_data
            if eval_data:
                queries = eval_data.get('queries', [])
                if queries:
                    passed = sum(1 for q in queries if q['passed'])
                    partial = sum(1 for q in queries if q.get('partial_credit', False))
                    total_passed += passed
                    total_partial += partial
                    total_evaluated += len(queries)
        
        # Get hardware info from first run
        first_run = self.runs_data[0] if self.runs_data else {}
        session_meta = first_run.get('session_meta', {})
        hardware = session_meta.get('hardware', 'Unknown')
        model = session_meta.get('model_tested', 'Unknown')
        
        # Extract quantization from model string or eval data
        quantization = 'Unknown'
        if model and ':' in model:
            quantization = model.split(':')[-1]
        elif self.evaluations_data:
            quantization = self.evaluations_data[0].get('quantization', 'Unknown')
        
        # Calculate overall statistics
        overall_accuracy = (total_passed / total_evaluated * 100) if total_evaluated > 0 else 0.0
        overall_accuracy_partial = ((total_passed + total_partial) / total_evaluated * 100) if total_evaluated > 0 else 0.0
        
        category_stats = self.calculate_category_statistics()
        
        summary = BenchmarkSummary(
            analysis_timestamp=datetime.now().isoformat(),
            total_runs=len(self.runs_data),
            total_queries=total_queries,
            total_evaluated=total_evaluated,
            overall_accuracy=round(overall_accuracy, 2),
            overall_accuracy_with_partial=round(overall_accuracy_partial, 2),
            avg_throughput=round(statistics.mean(all_tps), 2) if all_tps else 0.0,
            std_throughput=round(statistics.stdev(all_tps), 2) if len(all_tps) > 1 else 0.0,
            min_throughput=round(min(all_tps), 2) if all_tps else 0.0,
            max_throughput=round(max(all_tps), 2) if all_tps else 0.0,
            avg_generation_time=round(statistics.mean(all_gen_times), 2) if all_gen_times else 0.0,
            avg_cpu_temp_rise=round(statistics.mean(all_temp_rises), 2) if all_temp_rises else 0.0,
            total_throttling_events=total_throttling,
            hardware=hardware,
            model=model,
            quantization=quantization,
            categories=[asdict(c) for c in category_stats],
            run_statistics=[asdict(r) for r in run_stats]
        )
        
        return summary

    def print_summary(self, summary: BenchmarkSummary):
        """Print a formatted summary to console."""
        print("=" * 70)
        print("EDGE AI BENCHMARK ANALYSIS SUMMARY")
        print("=" * 70)
        print(f"\nAnalysis Timestamp: {summary.analysis_timestamp}")
        print(f"\nConfiguration:")
        print(f"  Hardware: {summary.hardware}")
        print(f"  Model: {summary.model}")
        print(f"  Quantization: {summary.quantization}")
        
        print(f"\nBenchmark Scope:")
        print(f"  Total Runs: {summary.total_runs}")
        print(f"  Total Queries: {summary.total_queries}")
        print(f"  Total Evaluated: {summary.total_evaluated}")
        print(f"  Evaluation Coverage: {summary.total_evaluated/summary.total_queries*100:.1f}%" if summary.total_queries > 0 else "  Evaluation Coverage: N/A")
        
        print(f"\nPerformance Metrics:")
        print(f"  Throughput: {summary.avg_throughput} tokens/s (σ={summary.std_throughput})")
        print(f"  Range: [{summary.min_throughput}, {summary.max_throughput}] tokens/s")
        print(f"  Avg Generation Time: {summary.avg_generation_time}s")
        
        print(f"\nHardware Metrics:")
        print(f"  Avg CPU Temp Rise: +{summary.avg_cpu_temp_rise}°C")
        print(f"  Throttling Events: {summary.total_throttling_events}")
        
        print(f"\nAccuracy Results:")
        print(f"  Strict Accuracy: {summary.overall_accuracy}%")
        print(f"  With Partial Credit: {summary.overall_accuracy_with_partial}%")
        
        print(f"\nCategory Performance:")
        print("-" * 70)
        print(f"{'Category':<40} {'Total':<8} {'Pass':<8} {'Accuracy':<10}")
        print("-" * 70)
        
        for cat in summary.categories:
            name = cat['category_name'][:38] + '..' if len(cat['category_name']) > 40 else cat['category_name']
            print(f"{name:<40} {cat['total_queries']:<8} {cat['passed']:<8} {cat['accuracy']:>6.1f}%")
        
        print("-" * 70)
        
        # Highlight top and bottom performers
        if summary.categories:
            print(f"\nTop Performers (>80% accuracy):")
            for cat in summary.categories:
                if cat['accuracy'] >= 80:
                    print(f"  ✓ {cat['category_name']} ({cat['accuracy']}%)")
            
            print(f"\nNeeds Improvement (<50% accuracy):")
            for cat in summary.categories:
                if cat['accuracy'] < 50:
                    print(f"  ✗ {cat['category_name']} ({cat['accuracy']}%)")
        
        print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description='Analyze Edge AI benchmark logs and generate statistics'
    )
    parser.add_argument(
        '--logs-dir', 
        default='session_logs',
        help='Directory containing JSON log files (default: session_logs)'
    )
    parser.add_argument(
        '--evals-dir',
        default='eval_q_a',
        help='Directory containing evaluation markdown files (default: eval_q_a)'
    )
    parser.add_argument(
        '--output',
        '-o',
        help='Output file path for JSON summary (optional)'
    )
    parser.add_argument(
        '--verbose',
        '-v',
        action='store_true',
        help='Enable verbose output'
    )
    
    args = parser.parse_args()
    
    # Resolve paths relative to script location or current directory
    script_dir = Path(__file__).parent
    logs_path = script_dir / args.logs_dir if not Path(args.logs_dir).is_absolute() else Path(args.logs_dir)
    evals_path = script_dir / args.evals_dir if not Path(args.evals_dir).is_absolute() else Path(args.evals_dir)
    
    if not logs_path.exists():
        print(f"Error: Logs directory not found: {logs_path}")
        return 1
    
    if not evals_path.exists():
        print(f"Warning: Evaluations directory not found: {evals_path}")
    
    try:
        analyzer = BenchmarkAnalyzer(str(logs_path), str(evals_path))
        
        if args.verbose:
            print(f"Loading logs from: {logs_path}")
        analyzer.load_json_logs()
        
        if args.verbose:
            print(f"Loading evaluations from: {evals_path}")
        analyzer.load_evaluation_markdowns()
        
        if args.verbose:
            print(f"Loaded {len(analyzer.runs_data)} runs and {len(analyzer.evaluations_data)} evaluation files")
        
        summary = analyzer.generate_summary()
        analyzer.print_summary(summary)
        
        if args.output:
            output_path = Path(args.output)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(asdict(summary), f, indent=2)
            print(f"\nSummary saved to: {output_path}")
        
        return 0
        
    except Exception as e:
        print(f"Error during analysis: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == '__main__':
    exit(main())
