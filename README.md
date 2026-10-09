# CPU Scheduling Simulation System

A beginner-friendly Streamlit application for comparing FCFS, non-preemptive SJF, SRTF, Priority Scheduling, Round Robin, and a two-queue Multilevel Queue scheduler.

## Run on Windows in VS Code

1. Open this folder in VS Code.
2. Use a 64-bit Python installation. In the integrated terminal, create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install the packages and start the app:

   ```powershell
   python -m pip install -r requirements.txt
   streamlit run app.py
   ```

The app opens in your browser. Use **Load sample processes**, edit the table, select algorithms and press **Run simulation**.

## Data and assumptions

The app can use any row-based **CSV, JSON, Excel (`.xlsx`/`.xlsm`), or SQLite** dataset. The included `bus_monitoring.db` remains available as a local example; it has bus, GPS, route, stop, and passenger records. SQLite files are opened read-only.

Open **Import rows from a dataset**, choose the local bus database or upload your own file, then choose a table if you selected SQLite. Each data row becomes one process. Map columns for process ID, arrival, CPU burst, priority, queue, and event description. You can use datasets from other projects as long as each row can represent a task or event.

If the source has an arrival timestamp, the app can convert it to minutes from the earliest row. Numeric arrival values are treated as integer simulation units. If no arrival column is selected, row order becomes arrival time. Missing burst, priority, or queue columns are filled with clearly identified simulated values; they are never described as measured CPU data. Existing mapped values are used as supplied and rounded to whole time units. Priority 1 is highest; queue 1 has strict precedence over queue 2.

Time values are integer simulation units. Priority 1 is highest. Multilevel Queue has three fixed-priority queues with configurable per-queue algorithms. Simultaneous arrivals are ordered by their order in the process table. A process's turnaround time is completion minus arrival, waiting time is turnaround minus burst, and response time is first CPU start minus arrival.

## Process manager controls

Use the sidebar to select a page, choose one of the six schedulers, set its mode or quantum, and start the simulation. Pause, resume, step through scheduling intervals, change playback speed, or reset the trace. The process page supports adding, editing, and deleting process rows before a run.

The dashboard and performance page report CPU utilization, throughput, context switches, average waiting time, average turnaround time, and average response time from the scheduler's event timeline. Context switches use zero simulated time. The timeline records arrivals, dispatches, preemptions, quantum expiry, idle periods, context switches, and termination. Process states are NEW, READY, RUNNING, WAITING, and TERMINATED; in this CPU-only model, WAITING means waiting for CPU service.

For Multilevel Queue, assign each process to queue 1, 2, or 3. Queue 1 has highest precedence. Choose FCFS, SJF, Priority, or Round Robin for each queue and choose whether higher queues can preempt lower queues. This is fixed multilevel queue scheduling, not multilevel feedback scheduling.

## Project files

- `app.py` — Streamlit interface, editable input table, database import, Gantt charts, and comparisons.
- `scheduler.py` — validated scheduler algorithms and performance calculations.
- `data_loader.py` — CSV, JSON, Excel, and read-only SQLite import and column mapping.
- `tests/test_scheduler.py` — algorithm and calculation checks.

Run the checks with `python -m unittest discover -s tests`.
