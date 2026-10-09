from __future__ import annotations

import base64
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from data_loader import (
    dataset_to_processes,
    inspect_database,
    read_dataset_file,
    read_sqlite_table,
)
from scheduler import ALGORITHMS, QUEUE_POLICIES, Process, simulate


st.set_page_config(
    page_title="Process Control Center",
    page_icon=":material/developer_board:",
    layout="wide",
)

SAMPLE = pd.DataFrame([
    {"Process": "P1", "Name": "System update", "Source Event": "Sample process", "Arrival": 0, "Burst": 5, "Priority": 3, "Queue": 1},
    {"Process": "P2", "Name": "Interactive task", "Source Event": "Sample process", "Arrival": 1, "Burst": 3, "Priority": 1, "Queue": 2},
    {"Process": "P3", "Name": "Background job", "Source Event": "Sample process", "Arrival": 2, "Burst": 8, "Priority": 4, "Queue": 3},
    {"Process": "P4", "Name": "Input handler", "Source Event": "Sample process", "Arrival": 3, "Burst": 2, "Priority": 2, "Queue": 2},
    {"Process": "P5", "Name": "File indexing", "Source Event": "Sample process", "Arrival": 5, "Burst": 4, "Priority": 2, "Queue": 3},
])

st.session_state.setdefault("processes", SAMPLE.copy())
st.session_state.setdefault("simulation", None)
st.session_state.setdefault("player_index", -1)
st.session_state.setdefault("is_running", False)
st.session_state.setdefault("dataset_notes", [])


def process_objects(frame: pd.DataFrame) -> list[Process]:
    required = ["Process", "Arrival", "Burst", "Priority", "Queue"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError("Missing process fields: " + ", ".join(missing))
    processes = []
    for _, row in frame.iterrows():
        if pd.isna(row["Process"]) or not str(row["Process"]).strip():
            continue
        name = row.get("Name", row["Process"])
        event = row.get("Source Event", "Manual task")
        processes.append(Process(
            pid=str(row["Process"]).strip(),
            name=str(name) if pd.notna(name) else str(row["Process"]),
            event=str(event) if pd.notna(event) else "Manual task",
            arrival=int(row["Arrival"]),
            burst=int(row["Burst"]),
            priority=int(row["Priority"]),
            queue=int(row["Queue"]),
        ))
    return processes


def gantt_figure(result: dict, *, height: int = 260):
    frame = pd.DataFrame([
        {"CPU": "Idle" if segment.pid is None else segment.pid,
         "Start": segment.start, "Finish": segment.end,
         "Interval": f"{segment.start}–{segment.end}"}
        for segment in result["segments"]
    ])
    figure = px.timeline(frame, x_start="Start", x_end="Finish", y="CPU", color="CPU", text="Interval")
    figure.update_yaxes(autorange="reversed", title="")
    figure.update_xaxes(title="Simulation time")
    figure.update_layout(height=height, showlegend=False, margin=dict(l=12, r=12, t=12, b=12))
    return figure


def comparison_frame(results: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Algorithm": result["algorithm"],
        "Avg waiting": result["averages"]["Waiting Time"],
        "Avg turnaround": result["averages"]["Turnaround Time"],
        "Avg response": result["averages"]["Response Time"],
        "CPU utilization (%)": result["cpu_utilization"],
        "Throughput": result["throughput"],
        "Context switches": result["context_switches"],
    } for result in results])


@st.cache_data(max_entries=6)
def cached_read_dataset(name: str, content: bytes) -> pd.DataFrame:
    return read_dataset_file(name, content)


@st.cache_data(max_entries=6)
def cached_database_info(content: bytes) -> dict:
    return inspect_database(content)


@st.cache_data(max_entries=12)
def cached_sqlite_table(content: bytes, table: str) -> pd.DataFrame:
    return read_sqlite_table(content, table)


with st.sidebar:
    st.markdown("## :material/developer_board: Process Control")
    st.caption("Educational OS scheduler · simulation only")
    page = st.radio(
        "System pages",
        ["Overview", "Processes", "CPU trace", "Performance", "Data import"],
        label_visibility="collapsed",
    )
    st.markdown("---")
    st.markdown("**Scheduler configuration**")
    selected_algorithm = st.selectbox("Scheduling algorithm", ALGORITHMS)
    quantum = st.number_input("Time quantum", min_value=1, value=2, step=1,
                              disabled=selected_algorithm not in ("Round Robin", "Multilevel Queue"))
    priority_mode = st.selectbox("Priority mode", ["Preemptive", "Non-preemptive"],
                                 disabled=selected_algorithm != "Priority Scheduling")
    queue_policies = ["Round Robin", "FCFS", "FCFS"]
    preempt_between_queues = True
    if selected_algorithm == "Multilevel Queue":
        st.caption("Queue 1 has the highest queue priority. Cross-queue context switches use zero simulated time.")
        queue_policies = [
            st.selectbox(f"Queue {q} policy", QUEUE_POLICIES,
                         index=(0 if q == 1 else 1), key=f"queue_policy_{q}")
            for q in (1, 2, 3)
        ]
        preempt_between_queues = st.checkbox("Higher queues may preempt lower queues", value=True)
    playback_speed = st.select_slider("Simulation speed", options=["0.5×", "1×", "2×"], value="1×")
    st.caption("Context-switch overhead: 0 simulation units")


st.title("Operating system / process manager")
st.caption("A process scheduling lab that models arrivals, ready queues, CPU dispatch, preemption, and completion. It does not control your computer's real CPU.")


def render_dataset_import() -> None:
    local_db = Path(__file__).with_name("bus_monitoring.db")
    st.subheader("Load a process dataset")
    st.write("Each selected row becomes a process. Import CSV, JSON, Excel, or SQLite data and map columns to scheduling inputs. Missing CPU fields are simulated and labeled.")
    source_options = (["Project bus_monitoring.db", "Upload a dataset"]
                      if local_db.is_file() else ["Upload a dataset"])
    source_mode = st.segmented_control("Dataset source", source_options, default=source_options[0])
    source_name = ""
    source_bytes = None
    if source_mode == "Project bus_monitoring.db":
        source_name = local_db.name
        source_bytes = local_db.read_bytes()
    else:
        uploaded = st.file_uploader(
            "Choose a CSV, JSON, XLSX/XLSM, or SQLite database",
            type=["csv", "json", "xlsx", "xlsm", "db", "sqlite", "sqlite3"],
        )
        if uploaded is not None:
            source_name = uploaded.name
            source_bytes = uploaded.getvalue()
    if source_bytes is None:
        st.info("Choose the project database or upload a row-based data file to continue.")
        return

    try:
        if Path(source_name).suffix.lower() in (".db", ".sqlite", ".sqlite3"):
            info = cached_database_info(source_bytes)
            if not info["tables"]:
                st.warning("No user tables were found in this database.")
                return
            table = st.selectbox("Database table", info["tables"])
            raw = cached_sqlite_table(source_bytes, table)
            st.caption("SQLite source is opened read-only.")
        else:
            raw = cached_read_dataset(source_name, source_bytes)
        columns = list(raw.columns)
        st.caption(f"{len(raw):,} rows · {len(columns)} columns · {source_name}")
        st.dataframe(raw.head(8), hide_index=True, alt="Preview of the imported dataset")

        def guess(candidates):
            by_name = {str(column).strip().lower(): column for column in columns}
            return next((by_name[name] for name in candidates if name in by_name), None)

        def pick_column(label, candidates, key):
            options = [None] + columns
            default = guess(candidates)
            return st.selectbox(
                label, options, index=options.index(default), key=key,
                format_func=lambda value: "Generate / none" if value is None else str(value),
            )

        with st.form("dataset_mapping", border=True):
            st.markdown("**Map dataset columns**")
            id_col = pick_column("Process ID", ["pid", "process_id", "process", "id"], "import_pid")
            arrival_col = pick_column("Arrival time", ["arrival", "arrival_time", "timestamp", "created_at", "recorded_at", "boarding_time"], "import_arrival")
            burst_col = pick_column("CPU burst", ["cpu_burst", "cpu_burst_time", "burst", "burst_time"], "import_burst")
            priority_col = pick_column("Priority", ["priority", "prio"], "import_priority")
            queue_col = pick_column("Queue number (1–3)", ["queue", "queue_id", "queue_assignment"], "import_queue")
            sample_arrival = raw[arrival_col].dropna().head(10) if arrival_col else pd.Series(dtype="object")
            timestamp_default = bool(
                arrival_col and not pd.api.types.is_numeric_dtype(raw[arrival_col])
                and pd.to_datetime(sample_arrival, errors="coerce", utc=True).notna().any()
            )
            is_timestamp = st.checkbox(
                "Convert timestamp arrivals to minutes from the earliest record",
                value=timestamp_default,
                disabled=arrival_col is None,
            )
            excluded = {id_col, arrival_col, burst_col, priority_col, queue_col, None}
            descriptions = st.multiselect(
                "Columns to show as the source event",
                columns,
                default=[column for column in columns if column not in excluded][:3],
            )
            st.caption("Mapped numeric values are rounded to whole simulation units. Missing burst, priority, and queue values are generated simulated values; priority 1 is highest.")
            import_clicked = st.form_submit_button("Import rows as processes", type="primary")

        if import_clicked:
            imported, notes = dataset_to_processes(
                raw,
                id_column=id_col,
                arrival_column=arrival_col,
                arrival_is_timestamp=is_timestamp,
                burst_column=burst_col,
                priority_column=priority_col,
                queue_column=queue_col,
                description_columns=descriptions,
            )
            st.session_state.processes = imported
            st.session_state.dataset_notes = notes
            st.session_state.simulation = None
            st.session_state.is_running = False
            st.session_state.player_index = -1
            st.session_state.pop("process_editor", None)
            st.success(f"Imported {len(imported):,} rows. Open **Processes** to review and edit them.")
    except Exception as exc:
        st.error(f"Could not load this dataset: {exc}")


if page == "Data import":
    render_dataset_import()
elif page == "Processes":
    st.subheader("Process table")
    st.caption("Add, edit, or delete processes before starting a simulation. Priority 1 is highest; queues 1–3 use ascending queue priority.")
    with st.container(horizontal=True):
        if st.button("Load sample processes", icon=":material/dataset:"):
            st.session_state.pop("process_editor", None)
            st.session_state.processes = SAMPLE.copy()
            st.session_state.simulation = None
            st.session_state.is_running = False
            st.session_state.player_index = -1
        if st.button("Clear process table", icon=":material/delete:"):
            st.session_state.pop("process_editor", None)
            st.session_state.processes = pd.DataFrame(columns=SAMPLE.columns)
            st.session_state.simulation = None
            st.session_state.is_running = False
            st.session_state.player_index = -1

    def save_processes() -> None:
        st.session_state.processes = st.session_state.process_editor
        st.session_state.simulation = None
        st.session_state.is_running = False
        st.session_state.player_index = -1

    st.data_editor(
        st.session_state.processes,
        num_rows="dynamic",
        key="process_editor",
        on_change=save_processes,
        width="stretch",
        alt="Editable process IDs, names, arrival and burst times, priorities, and queues",
    )
    if st.session_state.dataset_notes:
        with st.expander("Imported data assumptions"):
            for note in st.session_state.dataset_notes:
                st.write(f"• {note}")
elif page not in ("Overview", "CPU trace", "Performance", "Data import", "Processes"):
    st.info("Choose a section from the system menu.")


with st.sidebar:
    st.markdown("---")
    st.markdown("**Simulation controls**")
    start_clicked = st.button("Start simulation", type="primary", icon=":material/play_arrow:", width="stretch", disabled=page == "Data import")
    if start_clicked:
        try:
            process_list = process_objects(st.session_state.processes)
            chosen = simulate(
                process_list,
                selected_algorithm,
                quantum=int(quantum),
                priority_mode=priority_mode,
                queue_policies=queue_policies,
                preempt_between_queues=preempt_between_queues,
            )
            comparisons = [simulate(
                process_list,
                algorithm,
                quantum=int(quantum),
                priority_mode=priority_mode,
                queue_policies=queue_policies,
                preempt_between_queues=preempt_between_queues,
            ) for algorithm in ALGORITHMS]
            st.session_state.simulation = {"selected": chosen, "comparisons": comparisons}
            st.session_state.player_index = -1
            st.session_state.is_running = True
        except (ValueError, TypeError, KeyError) as exc:
            st.error(str(exc))
    current_simulation = st.session_state.simulation
    if current_simulation:
        if st.session_state.is_running:
            if st.button("Pause", icon=":material/pause:", width="stretch"):
                st.session_state.is_running = False
        elif st.session_state.player_index < len(current_simulation["selected"]["snapshots"]) - 1:
            if st.button("Resume", icon=":material/play_arrow:", width="stretch"):
                st.session_state.is_running = True
        if st.button("Step forward", icon=":material/skip_next:", width="stretch"):
            st.session_state.is_running = False
            st.session_state.player_index = min(
                st.session_state.player_index + 1,
                len(current_simulation["selected"]["snapshots"]) - 1,
            )
        if st.button("Reset simulation", icon=":material/restart_alt:", width="stretch"):
            st.session_state.is_running = False
            st.session_state.player_index = -1

    with st.expander("Info", icon=":material/info:"):
        st.markdown("**CPU Scheduling Simulator**")
        st.write("An educational project for exploring and comparing operating-system CPU scheduling algorithms.")
        profile_image = Path(__file__).with_name("profile.jpg")
        if profile_image.is_file():
            encoded_photo = base64.b64encode(profile_image.read_bytes()).decode("ascii")
            st.html(f"""
                <div style="display:flex;align-items:center;gap:12px;margin:8px 0 12px;">
                    <img src="data:image/jpeg;base64,{encoded_photo}"
                         alt="Profile photo of Anthony Gabriel Tolentino"
                         style="width:64px;height:64px;border-radius:50%;object-fit:cover;flex:0 0 64px;">
                    <div>
                        <div style="font-size:16px;font-weight:600;line-height:1.25;">
                            Anthony Gabriel Tolentino
                        </div>
                        <div style="font-size:13px;opacity:0.72;margin-top:4px;">
                            BS Computer Engineering
                        </div>
                    </div>
                </div>
            """)
        else:
            st.markdown("**Anthony Gabriel Tolentino**")
            st.caption("BS Computer Engineering")
        st.caption("For academic presentation and CPU scheduling simulation only.")


if page != "Data import":
    current_simulation = st.session_state.simulation
    playback_interval = {"0.5×": 1.2, "1×": 0.65, "2×": 0.3}[playback_speed]
    auto_interval = playback_interval if st.session_state.is_running else None

    @st.fragment(run_every=auto_interval)
    def playback_panel():
        simulation = st.session_state.simulation
        if not simulation:
            with st.container(border=True):
                st.markdown("**System status**")
                st.badge("IDLE", icon=":material/power_settings_new:", color="gray")
                st.caption("Configure processes, then start a simulation from the sidebar.")
            return

        snapshots = simulation["selected"]["snapshots"]
        if st.session_state.is_running:
            next_index = min(st.session_state.player_index + 1, len(snapshots) - 1)
            st.session_state.player_index = next_index
            if next_index == len(snapshots) - 1:
                st.session_state.is_running = False
        cursor = st.session_state.player_index
        if cursor < 0:
            snapshot = {
                "start": 0, "end": 0, "running": None, "ready": [], "completed": 0,
                "states": {process.pid: "NEW" for process in process_objects(st.session_state.processes)},
                "remaining": {process.pid: process.burst for process in process_objects(st.session_state.processes)},
                "event": "Simulation reset",
            }
            status = "IDLE"
        else:
            snapshot = snapshots[cursor]
            if cursor == len(snapshots) - 1:
                status = "TERMINATED"
            elif st.session_state.is_running:
                status = "RUNNING"
            else:
                status = "PAUSED"

        processes = process_objects(st.session_state.processes)
        by_pid = {process.pid: process for process in processes}
        state_counts = {state: sum(value == state for value in snapshot["states"].values())
                        for state in ("NEW", "READY", "RUNNING", "WAITING", "TERMINATED")}
        with st.container(border=True):
            header_col, status_col = st.columns([4, 1], vertical_alignment="center")
            with header_col:
                st.markdown(f"**System clock**　`{snapshot['start']}`　·　**CPU:** {snapshot['running'] or 'IDLE'}")
            with status_col:
                st.badge(status, color="green" if status == "RUNNING" else "gray")
            metrics = st.columns(5)
            metrics[0].metric("Running", state_counts["RUNNING"])
            metrics[1].metric("Ready", state_counts["READY"])
            metrics[2].metric("Waiting for CPU", state_counts["WAITING"])
            metrics[3].metric("Completed", state_counts["TERMINATED"])
            metrics[4].metric("Ready queue", len(snapshot["ready"]))
            st.caption("READY = arrived and not yet dispatched. WAITING = has run before and is queued for CPU service. I/O waits are not modeled.")
            queue_labels = [f"{pid} · {by_pid[pid].name or pid}" for pid in snapshot["ready"] if pid in by_pid]
            st.markdown("**Ready queue**　" + ("　›　".join(queue_labels) if queue_labels else "Empty"))
            live_rows = [{
                "PID": process.pid,
                "Name": process.name or process.pid,
                "State": snapshot["states"].get(process.pid, "NEW"),
                "Remaining burst": snapshot["remaining"].get(process.pid, process.burst),
                "Priority": process.priority,
                "Queue": process.queue,
            } for process in processes]
            st.dataframe(pd.DataFrame(live_rows), hide_index=True, alt="Live process states and remaining CPU burst")

    playback_panel()

    if current_simulation:
        chosen = current_simulation["selected"]
        comparisons = current_simulation["comparisons"]
        if page == "Overview":
            st.subheader("Performance overview")
            st.caption(f"{chosen['algorithm']} · {chosen['process_count']} processes · {chosen['total_time']} simulated units · context-switch cost is 0")
            with st.container(horizontal=True):
                st.metric("CPU utilization", f"{chosen['cpu_utilization']:.1f}%", border=True)
                st.metric("Total processes", chosen["process_count"], border=True)
                st.metric("Average waiting", f"{chosen['averages']['Waiting Time']:.2f}", border=True)
                st.metric("Average turnaround", f"{chosen['averages']['Turnaround Time']:.2f}", border=True)
                st.metric("Context switches", chosen["context_switches"], border=True)
            with st.container(horizontal=True):
                st.metric("Average response", f"{chosen['averages']['Response Time']:.2f}", border=True)
                st.metric("Throughput", f"{chosen['throughput']:.3f} processes/unit", border=True)
                st.metric("Busy CPU time", chosen["busy_time"], border=True)
                st.metric("Idle CPU time", chosen["idle_time"], border=True)
            first, second = st.columns([1.2, 1])
            with first.container(border=True):
                st.markdown("**CPU execution trace**")
                st.plotly_chart(gantt_figure(chosen, height=240), width="stretch",
                                alt=f"CPU execution timeline for {chosen['algorithm']}")
            with second.container(border=True):
                st.markdown("**Algorithm comparison**")
                comparison = comparison_frame(comparisons)
                chart = comparison.melt(
                    id_vars="Algorithm",
                    value_vars=["Avg waiting", "Avg turnaround", "Avg response"],
                    var_name="Metric", value_name="Average time",
                )
                st.plotly_chart(px.bar(chart, x="Algorithm", y="Average time", color="Metric", barmode="group"),
                                width="stretch", alt="Average scheduling times for the six algorithms")
            with st.container(border=True):
                st.markdown("**Completed processes**")
                st.dataframe(pd.DataFrame(chosen["rows"]), hide_index=True, alt="Completion and response results for each process")
        elif page == "CPU trace":
            st.subheader("CPU execution trace")
            st.caption("Timeline intervals and events are generated by the scheduler. Each interval is one playback step; context switches consume zero simulated time.")
            st.plotly_chart(gantt_figure(chosen, height=max(260, min(600, 35 * chosen["process_count"]))),
                            width="stretch", alt=f"Detailed CPU timeline for {chosen['algorithm']}")
            st.dataframe(pd.DataFrame(chosen["events"]), hide_index=True, alt="Arrivals, dispatches, preemptions, context switches, and completions")
        elif page == "Performance":
            st.subheader("Scheduling comparison")
            st.caption("Every algorithm uses the same process table, priority settings, quantum, and queue configuration.")
            comparison = comparison_frame(comparisons)
            with st.container(border=True):
                metric = st.selectbox("Compare metric", [
                    "Avg waiting", "Avg turnaround", "Avg response", "CPU utilization (%)", "Throughput", "Context switches",
                ])
                st.plotly_chart(px.bar(comparison, x="Algorithm", y=metric, color="Algorithm"),
                                width="stretch", alt=f"Comparison of {metric} for all six scheduling algorithms")
            st.dataframe(comparison, hide_index=True, alt="Performance measures for all six scheduling algorithms")
            st.caption("CPU utilization = busy time / elapsed simulation time. Throughput = completed processes / elapsed simulation time. Context-switch overhead is 0.")
    elif page in ("Overview", "CPU trace", "Performance"):
        st.info("No run is available yet. Add or import processes, choose an algorithm, and press **Start simulation** in the sidebar.")

if page == "Overview":
    with st.expander("Algorithm notes and simulation assumptions"):
        st.markdown("""
- **FCFS:** first arrived process runs to completion.
- **SJF:** shortest available original burst runs non-preemptively.
- **SRTF:** shortest remaining burst runs; a shorter arrival may preempt it.
- **Priority:** priority 1 is highest; choose preemptive or non-preemptive.
- **Round Robin:** ready tasks rotate after each configured time quantum.
- **Multilevel Queue:** queues 1–3 have fixed precedence; choose each queue's FCFS, SJF, Priority, or Round Robin policy. Cross-queue preemption can be enabled or disabled. This is not multilevel feedback scheduling.

The simulator models CPU service only; **WAITING** means waiting for CPU service. I/O blocking is not modeled. Context switches cost zero simulated time. Process and CPU statistics are computed from the shared event timeline. This is an educational simulation, not a real kernel.
""")
