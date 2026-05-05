import sys
import os
import re
from datetime import datetime
from typing import List, Optional

# Add parent directory to path to allow importing from 'db' and 'summarise'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import summarise as summarise_mod

try:
    from textual.app import App, ComposeResult
    from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
    from textual.widgets import (
        Header, Footer, Static, ListItem, ListView, 
        Input, Button, Label, DataTable, TabbedContent, TabPane, Tree
    )
    from textual.widgets.tree import TreeNode
    from textual.screen import Screen, ModalScreen
    from textual.binding import Binding
    from textual.message import Message
    from textual import on
    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False

# --- Helper: Time formatting ---
def _ts(epoch):
    if not epoch: return "?"
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")

# --- Dashboard Component ---
class Dashboard(Static):
    def on_mount(self) -> None:
        self.update_stats()
        self.set_interval(5.0, self.update_stats)

    def update_stats(self) -> None:
        try:
            conn = db.get_db()
            sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            unsummarised = conn.execute("SELECT COUNT(*) FROM messages WHERE summarised = 0").fetchone()[0]
            summaries = conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0]
            max_depth = db.get_max_summary_depth()
            
            db_size = os.path.getsize(str(db.VAULT_DB)) / (1024 * 1024) if db.VAULT_DB.exists() else 0
            provider = summarise_mod.get_provider_info()
            
            status_text = f"""
[bold blue]LCMv3 Vault Dashboard[/bold blue]

[cyan]Database Path:[/cyan] {db.VAULT_DB}
[cyan]File Size:[/cyan]     {db_size:.2f} MB

[green]Sessions:[/green]      {sessions}
[green]Messages:[/green]      {messages} ({unsummarised} pending)
[green]Summaries:[/green]     {summaries} (max depth: {max_depth})

[magenta]LLM Provider:[/magenta]  {provider.get('provider', 'None')} ({provider.get('model', 'N/A')})
"""
            self.update(status_text)
        except Exception as e:
            self.update(f"[red]Error loading stats: {e}[/red]")

# --- Session Components ---
class SessionItem(ListItem):
    def __init__(self, session: dict) -> None:
        super().__init__()
        self.session_data = session

    def compose(self) -> ComposeResult:
        sid = self.session_data['session_id']
        last = _ts(self.session_data.get('last_active'))
        yield Label(f"[bold]{sid[:35]}[/bold] [dim]({last})[/dim]")

class MessageView(ScrollableContainer):
    def display_session(self, session_id: str) -> None:
        self.query(Static).remove()
        msgs = db.get_messages_since(0)
        session_msgs = [m for m in msgs if m['session_id'] == session_id]
        
        for m in session_msgs:
            role_color = "green" if m['role'] == "user" else "cyan"
            ts = _ts(m['timestamp'])
            content = m['content'].replace("[", r"\[").replace("]", r"\]")
            self.mount(Static(f"[{role_color}][{ts}] {m['role'].upper()}:[/{role_color}]\n{content}\n"))
        self.scroll_end(animate=False)

# --- Search Components ---
class SearchView(Vertical):
    def compose(self) -> ComposeResult:
        yield Label("[bold]Global Search (FTS5)[/bold]")
        yield Input(placeholder="Type to search memories...", id="search-input")
        yield DataTable(id="search-results")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("ID", "Time", "Snippet")
        table.cursor_type = "row"

    @on(Input.Changed)
    def on_input_changed(self, event: Input.Changed) -> None:
        if len(event.value) < 2:
            self.query_one(DataTable).clear()
            return
        
        results = db.search_messages(event.value, limit=20)
        table = self.query_one(DataTable)
        table.clear()
        for r in results:
            ts = _ts(r['timestamp'])
            snippet = r['content'][:100].replace("\n", " ") + "..."
            table.add_row(f"msg:{r['id']}", ts, snippet, key=f"{r['session_id']}|{r['id']}")

# --- DAG Map Component ---
class DAGMapView(Horizontal):
    def compose(self) -> ComposeResult:
        yield Tree("DAG Heritage Map", id="dag-tree")
        yield ScrollableContainer(Static(id="dag-detail"), id="dag-detail-container")

    def on_mount(self) -> None:
        self.refresh_tree()

    def refresh_tree(self) -> None:
        tree = self.query_one(Tree)
        tree.clear()
        tree.root.expand()
        
        # 1. Find roots (summaries with max depth)
        max_d = db.get_max_summary_depth()
        if max_d < 0: return

        # Get all summaries grouped by depth for easier traversal
        all_sums = []
        try:
            conn = db.get_db()
            rows = conn.execute("SELECT * FROM summaries ORDER BY depth DESC").fetchall()
            all_sums = [dict(r) for r in rows]
        except Exception: return

        # Recursive builder
        def add_summary_node(parent_node: TreeNode, summary: dict):
            label = f"[bold magenta]D{summary['depth']}[/bold magenta] {summary['id'][:8]}"
            node = parent_node.add(label, data={"type": "summary", "id": summary['id']})
            
            # Find children (sources of this summary)
            sources = db.get_summary_sources(summary['id'])
            for src in sources:
                if src['source_type'] == 'summary':
                    # Find the summary object
                    child_sum = next((s for s in all_sums if s['id'] == src['source_id']), None)
                    if child_sum:
                        add_summary_node(node, child_sum)
                elif src['source_type'] == 'message':
                    node.add(f"[dim]msg:{src['source_id']}[/dim]", data={"type": "message", "id": src['source_id']})

        # Start from highest depth
        roots = [s for s in all_sums if s['depth'] == max_d]
        for r in roots:
            add_summary_node(tree.root, r)

    @on(Tree.NodeSelected)
    def on_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if not data: return
        
        detail = self.query_one("#dag-detail", Static)
        if data['type'] == 'summary':
            s = db.get_summary(data['id'])
            if s:
                content = s['content'].replace("[", r"\[").replace("]", r"\]")
                detail.update(f"[bold magenta]SUMMARY D{s['depth']}[/bold magenta] ({s['id']})\n\n{content}")
        elif data['type'] == 'message':
            # This is a bit expensive for v4, but good for UX
            try:
                conn = db.get_db()
                m = conn.execute("SELECT * FROM messages WHERE id = ?", (data['id'],)).fetchone()
                if m:
                    content = m['content'].replace("[", r"\[").replace("]", r"\]")
                    detail.update(f"[bold green]MESSAGE msg:{m['id']}[/bold green]\n\n{content}")
            except Exception: pass

# --- Dream Components ---
class DreamLogItem(ListItem):
    def __init__(self, log: dict) -> None:
        super().__init__()
        self.log_data = log

    def compose(self) -> ComposeResult:
        dt = _ts(self.log_data['dreamed_at'])
        p = self.log_data.get('patterns_found', 0)
        c = self.log_data.get('consolidations', 0)
        yield Label(f"[bold]{dt}[/bold] — [yellow]{p} patterns[/yellow], [green]{c} fixed[/green]")

class DreamView(Vertical):
    def on_mount(self) -> None:
        self.refresh_logs()

    def refresh_logs(self) -> None:
        try:
            conn = db.get_db()
            rows = conn.execute("SELECT * FROM dream_log ORDER BY dreamed_at DESC LIMIT 20").fetchall()
            logs = [dict(r) for r in rows]
            list_view = self.query_one("#dream-list", ListView)
            list_view.clear()
            for l in logs:
                list_view.append(DreamLogItem(l))
        except Exception:
            pass

    def display_report(self, report_path: str) -> None:
        report_display = self.query_one("#report-display", Static)
        if not report_path or not os.path.exists(report_path):
            report_display.update("[red]Report file not found.[/red]")
            return
        
        with open(report_path, "r", encoding="utf-8") as f:
            content = f.read().replace("[", r"\[").replace("]", r"\]")
            report_display.update(content)

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="dream-sidebar"):
                yield Label("[bold]Dream History[/bold]")
                yield ListView(id="dream-list")
            with ScrollableContainer(id="dream-content"):
                yield Static("[dim]Select a dream log to view report[/dim]", id="report-display")

# --- Main App ---
class LCMTUI(App):
    CSS = """
    Screen {
        background: $surface;
    }
    #sidebar {
        width: 35%;
        background: $panel;
        border-right: tall $primary;
        padding: 1;
    }
    #content {
        width: 65%;
        padding: 1;
    }
    #dag-tree {
        width: 40%;
        border-right: solid $primary;
    }
    #dag-detail-container {
        width: 60%;
        padding: 1;
    }
    #dream-sidebar {
        width: 35%;
        border-right: solid $primary;
        padding: 1;
    }
    #dream-content {
        width: 65%;
        padding: 1;
    }
    DataTable {
        height: 1fr;
    }
    ListItem {
        padding: 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("1", "switch_tab('sessions')", "Sessions", show=False),
        Binding("2", "switch_tab('search')", "Search", show=False),
        Binding("3", "switch_tab('dag')", "DAG Map", show=False),
        Binding("4", "switch_tab('stats')", "Stats", show=False),
        Binding("5", "switch_tab('dream')", "Dream", show=False),
        Binding("/", "focus_search", "Search", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="tabs"):
            with TabPane("Sessions", id="sessions"):
                yield Horizontal(
                    Vertical(
                        Label("[bold]Recent Sessions[/bold]"),
                        ListView(id="session-list"),
                        id="sidebar"
                    ),
                    MessageView(id="session-content")
                )
            with TabPane("Search", id="search"):
                yield SearchView()
            with TabPane("DAG Map", id="dag"):
                yield DAGMapView()
            with TabPane("Stats", id="stats"):
                yield Dashboard()
            with TabPane("Dream Logs", id="dream"):
                yield DreamView()
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_session_list()

    def refresh_session_list(self) -> None:
        sessions = db.list_sessions(limit=50)
        lv = self.query_one("#session-list", ListView)
        lv.clear()
        for s in sessions:
            lv.append(SessionItem(s))

    @on(ListView.Selected)
    def on_list_item_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, SessionItem):
            sid = event.item.session_data['session_id']
            self.query_one("#session-content", MessageView).display_session(sid)
        elif isinstance(event.item, DreamLogItem):
            path = event.item.log_data.get('report_path')
            self.query_one(DreamView).display_report(path)

    @on(DataTable.RowSelected, "#search-results")
    def on_search_result_selected(self, event: DataTable.RowSelected) -> None:
        # Jump to sessions tab and display the session
        # Parse the unique key back to session_id (format: session_id|message_id)
        session_id = str(event.row_key.value).split("|")[0]
        self.query_one("#tabs", TabbedContent).active = "sessions"
        self.query_one("#session-content", MessageView).display_session(session_id)

    def action_switch_tab(self, tab_id: str) -> None:
        self.query_one("#tabs", TabbedContent).active = tab_id

    def action_focus_search(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "search"
        self.query_one("#search-input", Input).focus()

    def action_refresh(self) -> None:
        self.query_one(Dashboard).update_stats()
        self.refresh_session_list()
        self.query_one(DreamView).refresh_logs()
        self.query_one(DAGMapView).refresh_tree()

if __name__ == "__main__":
    if not HAS_TEXTUAL:
        print("Error: 'textual' package not found. Run 'uv add textual'.")
        sys.exit(1)
    
    app = LCMTUI()
    app.run()
