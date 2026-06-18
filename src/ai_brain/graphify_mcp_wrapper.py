"""Graphify MCP server wrapper for dynamic project workspace detection.

Resolves the project workspace path (CWD) dynamically in IDE environments
and launches the graphify serve module.
"""
from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path

def discover_workspace() -> str | None:
    # 1. Try to get workspace from parent process siblings (for IDE environments like Antigravity)
    try:
        ppid = os.getppid()
        if ppid > 1:
            # Run ps to get PPID, PID, and Command for all processes
            output = subprocess.check_output(
                ["ps", "-ax", "-o", "ppid,pid,command"],
                universal_newlines=True
            )
            sibling_commands = []
            for line in output.strip().split("\n")[1:]:
                parts = line.strip().split(None, 2)
                if len(parts) < 3:
                    continue
                ppid_str, pid_str, cmd = parts
                try:
                    p_ppid = int(ppid_str)
                    if p_ppid == ppid:
                        sibling_commands.append(cmd)
                except ValueError:
                    continue
            
            workspace_id = None
            for cmd in sibling_commands:
                if "language_server" in cmd and "--workspace_id" in cmd:
                    parts = cmd.split()
                    for i, part in enumerate(parts):
                        if part == "--workspace_id" and i + 1 < len(parts):
                            workspace_id = parts[i + 1]
                            break
                        elif part.startswith("--workspace_id="):
                            workspace_id = part.split("=", 1)[1]
                            break
                    if workspace_id:
                        break
            
            if workspace_id:
                # Match against active projects list
                registry_path = Path.home() / ".claude" / "ai_brain_active_projects.txt"
                if registry_path.is_file():
                    with open(registry_path, "r", encoding="utf-8") as f:
                        active_projects = [l.strip() for l in f if l.strip()]
                    for proj in active_projects:
                        normalized = proj.strip("/")
                        expected_id = "file_" + normalized.replace("/", "_")
                        if expected_id == workspace_id:
                            return proj
    except Exception as e:
        sys.stderr.write(f"Error in process workspace discovery: {e}\n")

    # 2. Fallback: Search registered active projects by graph.json mtime
    try:
        registry_path = Path.home() / ".claude" / "ai_brain_active_projects.txt"
        if registry_path.is_file():
            with open(registry_path, "r", encoding="utf-8") as f:
                active_projects = [l.strip() for l in f if l.strip()]
            
            # Find the project with the most recently modified graphify-out/graph.json
            latest_mtime = -1
            latest_project = None
            for proj in active_projects:
                graph_path = Path(proj) / "graphify-out" / "graph.json"
                if graph_path.is_file():
                    mtime = graph_path.stat().st_mtime
                    if mtime > latest_mtime:
                        latest_mtime = mtime
                        latest_project = proj
            
            if latest_project:
                return latest_project
            
            # Fallback to the first existing project in the registry
            for proj in active_projects:
                if os.path.isdir(proj):
                    return proj
    except Exception as e:
        sys.stderr.write(f"Error in fallback workspace discovery: {e}\n")

    return None

def main() -> int:
    workspace = discover_workspace()
    if workspace:
        try:
            os.chdir(workspace)
            sys.stderr.write(f"Graphify MCP: successfully resolved workspace to {workspace}\n")
        except Exception as e:
            sys.stderr.write(f"Graphify MCP: failed to chdir to {workspace}: {e}\n")
    else:
        sys.stderr.write("Graphify MCP: could not resolve active workspace, running in default CWD\n")

    try:
        import graphify.serve
    except ImportError:
        sys.stderr.write("Error: graphify is not installed in the current python environment.\n")
        return 1

    graph_path = sys.argv[1] if len(sys.argv) > 1 else "graphify-out/graph.json"
    graphify.serve.serve(graph_path)
    return 0

if __name__ == "__main__":
    sys.exit(main())
