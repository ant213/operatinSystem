import unittest

from scheduler import ALGORITHMS, Process, simulate


class SchedulerTests(unittest.TestCase):
    def assert_bursts_complete_exactly(self, processes, result):
        ran = {p.pid: 0 for p in processes}
        for segment in result["segments"]:
            if segment.pid is not None:
                ran[segment.pid] += segment.end - segment.start
                source = next(p for p in processes if p.pid == segment.pid)
                self.assertGreaterEqual(segment.start, source.arrival)
        self.assertEqual(ran, {p.pid: p.burst for p in processes})
        self.assertEqual(len(result["rows"]), len(processes))
        self.assertTrue(all(row["State"] == "TERMINATED" for row in result["rows"]))

    def test_all_six_algorithms_complete(self):
        processes = [
            Process("P1", "first", 0, 5, 3, 1),
            Process("P2", "second", 1, 3, 1, 2),
            Process("P3", "third", 2, 8, 4, 3),
            Process("P4", "fourth", 3, 2, 2, 2),
            Process("P5", "fifth", 5, 4, 2, 3),
        ]
        for algorithm in ALGORITHMS:
            with self.subTest(algorithm=algorithm):
                result = simulate(processes, algorithm, quantum=2)
                self.assert_bursts_complete_exactly(processes, result)
                self.assertEqual(result["context_switch_overhead"], 0)
                self.assertGreaterEqual(result["cpu_utilization"], 0)
                self.assertGreater(result["throughput"], 0)

    def test_fcfs_handles_idle_period_and_metrics(self):
        processes = [Process("P1", "", 3, 2)]
        result = simulate(processes, "FCFS")
        self.assertEqual([(s.start, s.end, s.pid) for s in result["segments"]],
                         [(0, 3, None), (3, 5, "P1")])
        row = result["rows"][0]
        self.assertEqual((row["Start Time"], row["Completion Time"]), (3, 5))
        self.assertEqual((row["Turnaround Time"], row["Waiting Time"], row["Response Time"]), (2, 0, 0))
        self.assertEqual(result["cpu_utilization"], 40.0)
        self.assertEqual(result["throughput"], 0.2)
        self.assertIn("CPU idle", {snapshot["event"] for snapshot in result["snapshots"]})

    def test_sjf_uses_shortest_available_job(self):
        processes = [
            Process("P1", "", 0, 5), Process("P2", "", 1, 3), Process("P3", "", 2, 1)
        ]
        result = simulate(processes, "SJF")
        self.assertEqual([s.pid for s in result["segments"] if s.pid], ["P1", "P3", "P2"])

    def test_srtf_preempts_on_new_short_job(self):
        processes = [Process("P1", "", 0, 8), Process("P2", "", 2, 1)]
        result = simulate(processes, "SRTF")
        self.assertEqual([(s.start, s.end, s.pid) for s in result["segments"]],
                         [(0, 2, "P1"), (2, 3, "P2"), (3, 9, "P1")])
        self.assertIn("Preemption", [event["Event"] for event in result["events"]])

    def test_priority_ties_are_deterministic(self):
        processes = [Process("P1", "", 0, 3, 1), Process("P2", "", 1, 1, 1)]
        result = simulate(processes, "Priority Scheduling", priority_mode="Preemptive")
        self.assertEqual([(s.start, s.end, s.pid) for s in result["segments"]],
                         [(0, 3, "P1"), (3, 4, "P2")])
        np_result = simulate(processes, "Priority Scheduling", priority_mode="Non-preemptive")
        self.assertEqual(np_result["rows"][1]["Waiting Time"], 2)

    def test_round_robin_queue_order_and_exact_quantum_boundary(self):
        processes = [Process("P1", "", 0, 3), Process("P2", "", 1, 2)]
        result = simulate(processes, "Round Robin", quantum=2)
        self.assertEqual([(s.start, s.end, s.pid) for s in result["segments"]],
                         [(0, 2, "P1"), (2, 4, "P2"), (4, 5, "P1")])
        self.assertEqual(sum(e["Event"] == "Time quantum expired" for e in result["events"]), 1)

    def test_arrival_exactly_at_completion_does_not_preempt(self):
        processes = [Process("P1", "", 0, 2), Process("P2", "", 2, 1)]
        result = simulate(processes, "SRTF")
        self.assertEqual([(s.start, s.end, s.pid) for s in result["segments"]],
                         [(0, 2, "P1"), (2, 3, "P2")])
        self.assertEqual([e["Event"] for e in result["events"]].count("Preemption"), 0)

    def test_multilevel_queue_preemption_and_resume_order(self):
        processes = [Process("B", "batch", 0, 5, 2, 2), Process("S", "system", 2, 1, 1, 1)]
        result = simulate(processes, "Multilevel Queue", queue_policies=["FCFS", "FCFS", "FCFS"])
        self.assertEqual([(s.start, s.end, s.pid) for s in result["segments"]],
                         [(0, 2, "B"), (2, 3, "S"), (3, 6, "B")])
        no_preempt = simulate(processes, "Multilevel Queue", queue_policies=["FCFS"] * 3,
                              preempt_between_queues=False)
        self.assertEqual([(s.start, s.end, s.pid) for s in no_preempt["segments"]],
                         [(0, 5, "B"), (5, 6, "S")])

    def test_trace_contains_states_and_context_switches(self):
        processes = [Process("P1", "", 3, 3), Process("P2", "", 3, 1)]
        result = simulate(processes, "Round Robin", quantum=1)
        self.assertEqual(result["context_switches"], 2)
        state_names = {state for snapshot in result["snapshots"] for state in snapshot["states"].values()}
        event_states = {event["Details"] for event in result["events"]}
        self.assertTrue({"NEW", "READY", "WAITING", "RUNNING", "TERMINATED"}.issubset(state_names))
        self.assertIn("NEW → READY", event_states)
        self.assertEqual(result["snapshots"][-1]["states"], {"P1": "TERMINATED", "P2": "TERMINATED"})

    def test_invalid_values_are_rejected(self):
        with self.assertRaises(ValueError):
            simulate([Process("P1", "", -1, 1)], "FCFS")
        with self.assertRaises(ValueError):
            simulate([Process("P1", "", 0, 0)], "FCFS")
        with self.assertRaises(ValueError):
            simulate([Process("P1", "", 0, 1)], "Round Robin", quantum=0)
        with self.assertRaises(ValueError):
            simulate([Process("P1", "", 0, 1, queue=4)], "FCFS")


if __name__ == "__main__":
    unittest.main()
