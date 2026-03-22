from __future__ import annotations  # ✅ Fixed: double underscores
from typing import Optional
from rich.console import Console
from rich.text import Text
from rich.table import Table
from rich.panel import Panel
from rich.align import Align
from rich import box
import platform
import os
import threading
import time
from pathlib import Path
import shlex
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from core.scanner import run_glassbox

console = Console()
VERSION = "1.1.0"

# ✅ Fixed: String annotation for Observer
_livescan_observer: Optional["Observer"] = None
_livescan_lock = threading.Lock()

def show_welcome():
    ascii_art = r""" Welcome to...

  ██████  ██       █████  ███████ ███████ ██████   ██████  ██    ██
 ██       ██      ██   ██ ██      ██      ██   ██ ██    ██  ██  ██ 
 ██   ███ ██      ███████ ███████ ███████ ██████  ██    ██   ████  
 ██    ██ ██      ██   ██      ██      ██ ██   ██ ██    ██  ██  ██ 
  ██████  ███████ ██   ██ ███████ ███████ ██████   ██████  ██    ██
"""
    header = Panel(
        Align.center(Text(ascii_art, style="bold cyan")),
        title="GlassBox CLI",
        subtitle="Intelligent Codebase Documentation",
        border_style="cyan",
        box=box.ROUNDED,
        padding=(1, 4),
    )
    console.print()
    console.print(header)
    header_text = Text()
    header_text.append(f"Version {VERSION}", style="bold yellow")
    console.print(Align.center(header_text))
    console.print()
    status_table = Table(show_header=False, box=box.SIMPLE, expand=True)
    status_table.add_column("Key", style="bold", width=12)
    status_table.add_column("Value", justify="left")
    status_table.add_row("Python", platform.python_version())
    status_table.add_row("Platform", platform.system())
    status_table.add_row("Status", "[green]READY ✓[/green]")
    env_panel = Panel(
        status_table,
        title="Environment",
        border_style="green",
        box=box.ROUNDED,
        height=8
    )
    command_table = Table(show_header=True, header_style="bold magenta", box=box.SIMPLE, expand=True)
    command_table.add_column("Command", style="cyan", no_wrap=True)
    command_table.add_column("Shortcut", style="yellow")
    command_table.add_column("Description", justify="left")
    command_table.add_row("--scan", "-scn", "One-time scan and document")
    command_table.add_row("--livescan", "-lscn", "Live document sync")
    command_table.add_row("--stop", "-stp", "Stop process")
    cmd_panel = Panel(
        command_table,
        title="Available Commands",
        border_style="cyan",
        box=box.ROUNDED,
        height=8
    )
    layout_grid = Table.grid(expand=True, padding=1)
    layout_grid.add_column(ratio=1)
    layout_grid.add_column(ratio=1)
    layout_grid.add_row(env_panel, cmd_panel)
    console.print(layout_grid)
    console.print()
    console.print(Align.center("[bold cyan]Type command below to begin.[/bold cyan]"))
    console.print()

SUPPORTED_EXTENSIONS = {".py", ".java", ".cpp", ".c"}

def _resolve_path(raw: str) -> Optional[Path]:
    p = Path(raw).expanduser().resolve()
    if not p.exists():
        console.print(f"[red]✗ Path does not exist:[/red] {p}")
        return None
    if not p.is_dir():
        console.print(f"[red]✗ Path is not a directory:[/red] {p}")
        return None
    has_sources = any(
        f.suffix in SUPPORTED_EXTENSIONS
        for f in p.rglob("*")
        if f.is_file()
    )
    if not has_sources:
        console.print(f"[yellow]⚠ No supported source files (.py .java .cpp .c) found in:[/yellow] {p}")
        console.print("[yellow]  Scan will run but may find nothing.[/yellow]")
    return p

def _parse_path_arg(args: list) -> Optional[str]:
    if "-p" not in args:
        console.print("[red]✗ Missing required flag:[/red] [cyan]-p  <project_path>[/cyan]")
        console.print("[dim]  Example: glassbox --scan -p ./my_project[/dim]")
        return None
    idx = args.index("-p")
    if idx + 1 >= len(args):
        console.print("[red]✗ No path provided after[/red] [cyan]-p[/cyan]")
        return None
    return args[idx + 1]

class _SourceChangeHandler(FileSystemEventHandler):
    def __init__(self, project_path: Path, debounce: float = 3.0):
        super().__init__()
        self.project_path = project_path
        self.debounce     = debounce
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _is_source(self, path: str) -> bool:
        return Path(path).suffix in SUPPORTED_EXTENSIONS

    def _schedule_scan(self, path: str):
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce, self._run_scan, args=[path])
            self._timer.daemon = True
            self._timer.start()

    def _run_scan(self, changed_path: str):
        console.print(f"\n[bold cyan]↺  Change detected:[/bold cyan] {Path(changed_path).name}")
        console.print("[dim]  Re-scanning...[/dim]")
        try:
            run_glassbox(str(self.project_path), console)
        except Exception as e:
            console.print(f"[bold red]✗ Scan error:[/bold red] {e}")
        console.print("[bold green]✓ Live scan complete.[/bold green]")
        console.print("[dim]  Watching for changes... (glassbox --stop to stop)[/dim]")
        # ── Reprint prompt so user knows CLI is ready ──────────────────────────
        console.print("\n[bold green]glassbox  > [/bold green]", end="")

    def on_modified(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        # Skip hidden files, editor temp files, cache files
        if path.name.startswith(".") or path.suffix in {".swp", ".tmp", ".pyc"} \
                or path.name == "cache_store.json":
            return
        if self._is_source(event.src_path):
            self._schedule_scan(event.src_path)

    def on_created(self, event):
        if not event.is_directory and self._is_source(event.src_path):
            self._schedule_scan(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory and self._is_source(event.src_path):
            console.print(f"\n[yellow]⚠  File deleted:[/yellow] {Path(event.src_path).name} [dim](no re-scan needed)[/dim]")

def _start_livescan(project_path: Path):
    global _livescan_observer
    with _livescan_lock:
        if _livescan_observer and _livescan_observer.is_alive():
            console.print("[yellow]⚠ Live scan is already running.[/yellow]")
            console.print("[dim]  Use 'glassbox --stop' to stop it first.[/dim]")
            return
        console.print("\n[bold cyan]Running initial scan...[/bold cyan]")
        try:
            run_glassbox(str(project_path), console)
        except Exception as e:
            console.print(f"[bold red]✗ Initial scan failed:[/bold red] {e}")
            return
        handler = _SourceChangeHandler(project_path)
        observer = Observer()
        observer.schedule(handler, str(project_path), recursive=True)
        observer.daemon = True
        observer.start()
        _livescan_observer = observer
    console.print(f"\n[bold green]✓ Live scan active[/bold green] — watching [cyan]{project_path}[/cyan]")
    console.print("[dim]  Changes to .py .java .cpp .c files will trigger re-scan.[/dim]")
    console.print("[dim]  Use 'glassbox --stop' to stop.[/dim]\n")

def _stop_livescan():
    global _livescan_observer
    with _livescan_lock:
        if _livescan_observer is None or not _livescan_observer.is_alive():
            console.print("[yellow]⚠ No live scan is currently running.[/yellow]")
            return
        _livescan_observer.stop()
        _livescan_observer.join(timeout=5)
        _livescan_observer = None
    console.print("[bold green]✓ Live scan stopped.[/bold green]")

def start_shell():
    show_welcome()
    while True:
        try:
            user_input = console.input("[bold green]glassbox  > [/bold green]").strip()
            if not user_input:
                continue
            try:
                args = shlex.split(user_input)
            except ValueError as e:
                console.print(f"[red]✗ Invalid input:[/red] {e}")
                continue
            cmd = args[0].lower()
            if cmd in ("exit", "quit"):
                if _livescan_observer and _livescan_observer.is_alive():
                    console.print("[dim]Stopping live scan...[/dim]")
                    _stop_livescan()
                console.print("\n[bold yellow]Exiting GlassBox...[/bold yellow]\n")
                break
            if cmd in ("cls", "clear"):
                os.system("cls" if os.name == "nt" else "clear")
                continue
            if cmd != "glassbox":
                console.print(f"[red]✗ Unknown command:[/red] [bold]{cmd}[/bold]  [dim](commands start with 'glassbox')[/dim]")
                continue
            if "--scan" in args or "-scn" in args:
                raw = _parse_path_arg(args)
                if not raw:
                    continue
                project_path = _resolve_path(raw)
                if not project_path:
                    continue
                console.print(f"\n[bold cyan]Starting GlassBox scan...[/bold cyan]\n")
                try:
                    run_glassbox(str(project_path), console)
                except KeyboardInterrupt:
                    console.print("\n[yellow]⚠ Scan interrupted by user.[/yellow]")
                except Exception as e:
                    console.print(f"\n[bold red]✗ Scan failed:[/bold red] {e}")
                else:
                    console.print("\n[bold green]✓ Scan completed.[/bold green]\n")
            elif "--livescan" in args or "-lscn" in args:
                raw = _parse_path_arg(args)
                if not raw:
                    continue
                project_path = _resolve_path(raw)
                if not project_path:
                    continue
                _start_livescan(project_path)
            elif "--stop" in args or "-stp" in args:
                _stop_livescan()
            elif "--help" in args or "-h" in args:
                help_table = Table(show_header=True, header_style="bold magenta", box=box.SIMPLE)
                help_table.add_column("Command",     style="cyan", no_wrap=True)
                help_table.add_column("Flags",       style="yellow")
                help_table.add_column("Description")
                help_table.add_row("glassbox --scan", "-scn -p  <path>", "One-time scan and document a project")
                help_table.add_row("glassbox --livescan", "-lscn -p  <path>", "Watch project and re-scan on file changes")
                help_table.add_row("glassbox --stop", "-stp", "Stop an active live scan")
                help_table.add_row("exit / quit", "", "Exit GlassBox")
                help_table.add_row("clear / cls", "", "Clear the terminal")
                console.print(Panel(help_table, title="Help", border_style="cyan", box=box.ROUNDED))
            else:
                console.print("[red]✗ Invalid glassbox command.[/red]  [dim]Try: glassbox --help[/dim]")
        except KeyboardInterrupt:
            console.print("\n[yellow]⚠ Use 'exit' to quit or Ctrl+C again to force.[/yellow]")
            try:
                time.sleep(0.5)
            except KeyboardInterrupt:
                if _livescan_observer and _livescan_observer.is_alive():
                    _stop_livescan()
                console.print("\n[bold yellow]Session terminated.[/bold yellow]\n")
                break

if __name__ == "__main__":
    start_shell()