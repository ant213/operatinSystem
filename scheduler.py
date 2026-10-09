"""Deterministic CPU scheduling engine with a shared event and playback trace."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Process:
    pid: str
    event: str
    arrival: int
    burst: int
    priority: int = 1  # 1 is highest priority
    queue: int = 1
    name: str = ""


@dataclass(frozen=True)
class Segment:
    start: int
    end: int
    pid: str | None  # None means the CPU is idle


ALGORITHMS = (
    "FCFS", "SJF", "SRTF", "Priority Scheduling", "Round Robin", "Multilevel Queue"
)
QUEUE_POLICIES = ("FCFS", "SJF", "Priority", "Round Robin")


def validate_processes(processes: Iterable[Process]) -> list[Process]:
    items = list(processes)
    if not items:
        raise ValueError("Add at least one process before running the simulation.")
    seen: set[str] = set()
    for p in items:
        if not p.pid.strip():
            raise ValueError("Process IDs cannot be blank.")
        if p.pid in seen:
            raise ValueError(f"Process ID {p.pid!r} is duplicated.")
        seen.add(p.pid)
        if p.arrival < 0:
            raise ValueError(f"{p.pid}: arrival time must be zero or greater.")
        if p.burst <= 0:
            raise ValueError(f"{p.pid}: CPU burst must be greater than zero.")
        if p.priority < 1:
            raise ValueError(f"{p.pid}: priority must be 1 or greater (1 is highest).")
        if p.queue not in (1, 2, 3):
            raise ValueError(f"{p.pid}: queue assignment must be 1, 2, or 3.")
    return items


def _append(segments: list[Segment], start: int, end: int, pid: str | None) -> None:
    if end <= start:
        return
    if segments and segments[-1].pid == pid and segments[-1].end == start:
        previous = segments[-1]
        segments[-1] = Segment(previous.start, end, pid)
    else:
        segments.append(Segment(start, end, pid))


def _metrics(name: str, processes: list[Process], segments: list[Segment],
             context_switches: int, events: list[dict], snapshots: list[dict]) -> dict:
    completion: dict[str, int] = {}
    first_start: dict[str, int] = {}
    busy_time = sum(s.end - s.start for s in segments if s.pid is not None)
    end_time = max((s.end for s in segments), default=0)
    for segment in segments:
        if segment.pid is not None:
            first_start.setdefault(segment.pid, segment.start)
            completion[segment.pid] = segment.end

    rows = []
    for process in processes:
        ct = completion[process.pid]
        turnaround = ct - process.arrival
        rows.append({
            "Process": process.pid,
            "Name": process.name or process.pid,
            "Source Event": process.event,
            "Arrival": process.arrival,
            "Burst": process.burst,
            "Remaining Burst": 0,
            "Priority": process.priority,
            "Queue": process.queue,
            "Start Time": first_start[process.pid],
            "Completion Time": ct,
            "Turnaround Time": turnaround,
            "Waiting Time": turnaround - process.burst,
            "Response Time": first_start[process.pid] - process.arrival,
            "State": "TERMINATED",
        })
    averages = {
        field: sum(row[field] for row in rows) / len(rows)
        for field in ("Turnaround Time", "Waiting Time", "Response Time")
    }
    return {
        "algorithm": name,
        "segments": segments,
        "rows": rows,
        "averages": averages,
        "events": events,
        "snapshots": snapshots,
        "busy_time": busy_time,
        "total_time": end_time,
        "idle_time": end_time - busy_time,
        "cpu_utilization": (busy_time / end_time * 100) if end_time else 0.0,
        "throughput": (len(processes) / end_time) if end_time else 0.0,
        "context_switches": context_switches,
        "context_switch_overhead": 0,
        "process_count": len(processes),
    }


def simulate(
    processes: Iterable[Process],
    algorithm: str,
    *,
    quantum: int = 2,
    priority_mode: str = "Preemptive",
    queue_policies: Iterable[str] | None = None,
    preempt_between_queues: bool = True,
) -> dict:
    """Run a scheduler and return metrics, Gantt segments, events, and step snapshots.

    One snapshot represents a scheduling interval, so stepping follows meaningful
    dispatch, completion, preemption, quantum, and idle boundaries rather than
    iterating over arbitrarily large timestamp gaps.
    """
    items = validate_processes(processes)
    if algorithm not in ALGORITHMS:
        raise ValueError(f"Unknown algorithm: {algorithm}")
    if quantum <= 0:
        raise ValueError("Time quantum must be greater than zero.")
    if priority_mode not in ("Preemptive", "Non-preemptive"):
        raise ValueError("Priority mode must be Preemptive or Non-preemptive.")

    policies = list(queue_policies or ("Round Robin", "FCFS", "FCFS"))
    policies = (policies + ["FCFS"] * 3)[:3]
    if any(policy not in QUEUE_POLICIES for policy in policies):
        raise ValueError("Queue policies must be FCFS, SJF, Priority, or Round Robin.")

    count = len(items)
    remaining = [p.burst for p in items]
    has_run = [False] * count
    states = ["NEW"] * count
    arrived: set[int] = set()
    ready: list[int] = []
    completed: set[int] = set()
    time = 0
    segments: list[Segment] = []
    events: list[dict] = []
    snapshots: list[dict] = []
    first_start: dict[int, int] = {}
    context_switches = 0
    previous_cpu: int | None = None
    pending_requeue: tuple[int, str] | None = None

    def record(event_time: int, event: str, index: int | None = None, detail: str = "") -> None:
        events.append({
            "Time": event_time,
            "Event": event,
            "Process": items[index].pid if index is not None else "—",
            "Details": detail,
        })

    def admit_until(now: int) -> None:
        new_indices = sorted(
            (i for i, p in enumerate(items) if i not in arrived and p.arrival <= now),
            key=lambda i: (items[i].arrival, i),
        )
        for i in new_indices:
            arrived.add(i)
            states[i] = "READY"
            ready.append(i)
            record(items[i].arrival, "Process arrived", i, "NEW → READY")

    def policy_key(i: int, selected_policy: str) -> tuple:
        process = items[i]
        if selected_policy == "SJF":
            return process.burst, process.arrival, i
        if selected_policy == "Priority":
            return process.priority, process.arrival, i
        return process.arrival, i

    def choose_ready() -> int:
        if algorithm == "Round Robin":
            return ready.pop(0)
        if algorithm == "FCFS":
            selected = min(ready, key=lambda i: (items[i].arrival, i))
        elif algorithm == "SJF":
            selected = min(ready, key=lambda i: policy_key(i, "SJF"))
        elif algorithm == "SRTF":
            selected = min(ready, key=lambda i: (remaining[i], items[i].arrival, i))
        elif algorithm == "Priority Scheduling":
            selected = min(ready, key=lambda i: (items[i].priority, items[i].arrival, i))
        else:
            highest_queue = min(items[i].queue for i in ready)
            queue_ready = [i for i in ready if items[i].queue == highest_queue]
            queue_policy = policies[highest_queue - 1]
            if queue_policy == "Round Robin":
                selected = min(queue_ready, key=lambda i: (items[i].arrival, i))
            else:
                selected = min(queue_ready, key=lambda i: policy_key(i, queue_policy))
        ready.remove(selected)
        return selected

    while len(completed) < count:
        admit_until(time)
        if not ready:
            future = [i for i in range(count) if i not in arrived]
            if not future:
                raise RuntimeError("Scheduler stopped with unfinished processes and no ready work.")
            next_time = min(items[i].arrival for i in future)
            _append(segments, time, next_time, None)
            record(time, "CPU idle", detail=f"No ready process until time {next_time}")
            snapshots.append({
                "start": time, "end": next_time, "running": None, "ready": [],
                "states": {items[i].pid: states[i] for i in range(count)},
                "remaining": {items[i].pid: remaining[i] for i in range(count)},
                "completed": len(completed), "event": "CPU idle",
            })
            time = next_time
            previous_cpu = None
            continue

        index = choose_ready()
        process = items[index]
        if pending_requeue is not None:
            previous_index, reason = pending_requeue
            if previous_index != index and reason in ("preemption", "arrival"):
                record(time, "Preemption", previous_index,
                       f"RUNNING → WAITING; {items[index].pid} selected by the scheduler")
            pending_requeue = None

        if previous_cpu is not None and previous_cpu != index:
            context_switches += 1
            record(time, "Context switch", index, f"{items[previous_cpu].pid} → {process.pid}; zero-time overhead")
        if index not in first_start:
            first_start[index] = time
        prior_state = states[index]
        record(time, "Dispatch", index, f"{prior_state} → RUNNING; {process.name or process.pid}")
        states[index] = "RUNNING"

        run_for = remaining[index]
        boundary_reason = "completion"
        if algorithm == "Round Robin":
            run_for = min(run_for, quantum)
            boundary_reason = "quantum" if run_for < remaining[index] or run_for == quantum else "completion"
        elif algorithm == "SRTF":
            arrivals = [p.arrival for i, p in enumerate(items) if i not in arrived and p.arrival > time]
            if arrivals and min(arrivals) - time < run_for:
                run_for = min(arrivals) - time
                boundary_reason = "arrival"
        elif algorithm == "Priority Scheduling" and priority_mode == "Preemptive":
            arrivals = [p.arrival for i, p in enumerate(items) if i not in arrived and p.arrival > time]
            if arrivals and min(arrivals) - time < run_for:
                run_for = min(arrivals) - time
                boundary_reason = "arrival"
        elif algorithm == "Multilevel Queue":
            queue_policy = policies[process.queue - 1]
            if queue_policy == "Round Robin":
                run_for = min(run_for, quantum)
                if run_for < remaining[index]:
                    boundary_reason = "quantum"
            if preempt_between_queues:
                higher_arrivals = [
                    p.arrival for i, p in enumerate(items)
                    if i not in arrived and p.arrival > time and p.queue < process.queue
                ]
                if higher_arrivals and min(higher_arrivals) - time < run_for:
                    run_for = min(higher_arrivals) - time
                    boundary_reason = "queue arrival"

        queued_states = {items[i].pid: states[i] for i in range(count)}
        for i in ready:
            queued_states[items[i].pid] = "WAITING" if has_run[i] else "READY"
        snapshots.append({
            "start": time,
            "end": time + run_for,
            "running": process.pid,
            "ready": [items[i].pid for i in ready],
            "states": queued_states,
            "remaining": {items[i].pid: remaining[i] for i in range(count)},
            "completed": len(completed),
            "event": "CPU executing",
        })
        _append(segments, time, time + run_for, process.pid)
        remaining[index] -= run_for
        has_run[index] = True
        time += run_for
        previous_cpu = index
        admit_until(time)

        if remaining[index] == 0:
            states[index] = "TERMINATED"
            completed.add(index)
            record(time, "Process completed", index, "RUNNING → TERMINATED")
        else:
            states[index] = "WAITING"
            if boundary_reason == "quantum":
                ready.append(index)
                record(time, "Time quantum expired", index, f"RUNNING → WAITING; returned to queue with {remaining[index]} units left")
                pending_requeue = (index, "quantum")
            elif boundary_reason == "queue arrival":
                ready.insert(0, index)
                record(time, "Queue preemption", index, "RUNNING → WAITING; a higher-priority queue became ready")
                pending_requeue = (index, "queue")
            else:
                ready.append(index)
                pending_requeue = (index, "preemption")

    final_states = {items[i].pid: "TERMINATED" for i in range(count)}
    snapshots.append({
        "start": time, "end": time, "running": None, "ready": [],
        "states": final_states,
        "remaining": {items[i].pid: 0 for i in range(count)},
        "completed": count, "event": "All processes terminated",
    })
    events.append({"Time": time, "Event": "Simulation complete", "Process": "—", "Details": f"{count} processes terminated"})

    name = algorithm
    if algorithm == "Priority Scheduling":
        name = f"Priority Scheduling ({priority_mode})"
    if algorithm == "Multilevel Queue":
        name = "Multilevel Queue"
    return _metrics(name, items, segments, context_switches, events, snapshots)
