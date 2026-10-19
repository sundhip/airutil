"""Docker Terminal Showcase Engine: Whitelist Parser & Read-Only Dispatcher.

Operating Systems & Container Concepts Demonstrated:
1. Least Privilege Security Boundary: Read-only interaction with Docker daemon API
   via an isolated socket proxy container (airutil_dockerproxy).
2. Strict Whitelist Tokenization: Safe parsing via shlex.split with zero shell spawning,
   preventing command injection and arbitrary subprocess execution.
3. Live Telemetry Streaming: Server-Sent Events (SSE) streaming output in real time.
4. Container Namespaces & cgroups Introspection: Direct inspection of isolated process
   namespaces and CPU/memory quotas across container replicas.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import time
from typing import Any, Dict, Generator, List, Optional, Tuple

import requests
from app.db import log_activity
from app.session import get_redis_client

logger = logging.getLogger("airutil.showcase")

DOCKER_HOST = os.getenv("DOCKER_HOST", "tcp://dockerproxy:2375")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "airutil_internal_telemetry_token_64bytes_production")

# Canonical airutil container prefix and known containers
ALLOWED_CONTAINER_PREFIX = "airutil_"
KNOWN_CONTAINERS = {
    "airutil_nginx",
    "airutil_app1",
    "airutil_app2",
    "airutil_worker",
    "airutil_redis",
    "airutil_db",
    "airutil_dockerproxy",
}

# Standard ANSI Color Escapes for xterm.js
ANSI_RESET = "\x1b[0m"
ANSI_BOLD = "\x1b[1m"
ANSI_CYAN = "\x1b[36m"
ANSI_GREEN = "\x1b[32m"
ANSI_YELLOW = "\x1b[33m"
ANSI_RED = "\x1b[31m"
ANSI_BLUE = "\x1b[34m"
ANSI_MAGENTA = "\x1b[35m"
ANSI_DIM = "\x1b[2m"

HEADER_NOTICE = f"{ANSI_DIM}# Rendered from Docker Engine API (read-only proxy) | airutil_dockerproxy{ANSI_RESET}\r\n"


def validate_command(cmd_text: str) -> Tuple[bool, str]:
    """Validate command string against strict whitelist rules without executing."""
    clean_cmd = cmd_text.strip()
    if not clean_cmd:
        return False, "Empty command"
    forbidden = [";", "&&", "||", "|", ">", "<", "$(", "`", "\n", "\r"]
    for char in forbidden:
        if char in clean_cmd:
            return False, f"Forbidden shell token: {char}"
    try:
        tokens = shlex.split(clean_cmd)
    except Exception as e:
        return False, f"Syntax error: {e}"
    if not tokens:
        return False, "Empty command"
    verb = tokens[0].lower()
    if verb in ("help", "clear"):
        return True, "OK"
    elif verb == "docker":
        if len(tokens) < 2:
            return True, "Usage"
        sub = tokens[1].lower()
        if sub in ("ps", "stats", "inspect", "top", "network", "volume", "logs", "images", "info"):
            return True, "OK"
        return False, f"Unknown docker subcommand: {sub}"
    elif verb in ("os", "demo"):
        return True, "OK"
    return False, f"Command '{verb}' not permitted"


def get_proxy_base_url() -> str:
    """Normalize docker proxy URL to standard HTTP endpoint."""
    raw = DOCKER_HOST.strip()
    if raw.startswith("tcp://"):
        return "http://" + raw[6:]
    return raw


def check_rate_limit(user_id: int) -> Tuple[bool, int]:
    """Enforce 30 commands per minute per user via Redis.

    Returns:
        (is_allowed, remaining_window_seconds)
    """
    r = get_redis_client()
    key = f"ratelimit:showcase:{user_id}"
    try:
        current = r.get(key)
        if current and int(current) >= 30:
            ttl = r.ttl(key)
            return False, max(ttl, 1)

        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 60)
        pipe.execute()
        return True, 0
    except Exception as e:
        logger.warning("Showcase rate limit check failed: %s", e)
        return True, 0


def format_table(headers: List[str], rows: List[List[str]]) -> str:
    """Format tabular data with clean aligned columns."""
    if not rows:
        return "No entries found.\r\n"
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(str(val)))

    header_line = "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    sep_line = "  ".join("-" * col_widths[i] for i in range(len(headers)))
    lines = [f"{ANSI_BOLD}{header_line}{ANSI_RESET}", f"{ANSI_DIM}{sep_line}{ANSI_RESET}"]
    for row in rows:
        line_parts = []
        for i, val in enumerate(row):
            if i < len(col_widths):
                line_parts.append(str(val).ljust(col_widths[i]))
            else:
                line_parts.append(str(val))
        lines.append("  ".join(line_parts))
    return "\r\n".join(lines) + "\r\n"


class ShowcaseEngine:
    """Handles parsing, whitelist validation, and execution of showcase commands."""

    def __init__(self, user_id: int, client_ip: str = "127.0.0.1"):
        self.user_id = user_id
        self.client_ip = client_ip
        self.proxy_url = get_proxy_base_url()

    def run_command_stream(self, cmd_text: str) -> Generator[str, None, None]:
        """Parse command and yield output chunks formatted with ANSI escape sequences."""
        yield HEADER_NOTICE

        # 1. Check Rate Limit
        allowed, wait_sec = check_rate_limit(self.user_id)
        if not allowed:
            yield f"{ANSI_RED}Rate limit exceeded: maximum 30 commands per minute. Please wait {wait_sec}s.{ANSI_RESET}\r\n"
            return

        clean_cmd = cmd_text.strip()
        if not clean_cmd:
            return

        # 2. Tokenize with shlex
        try:
            tokens = shlex.split(clean_cmd)
        except Exception as e:
            yield f"{ANSI_RED}Syntax error parsing command: {e}{ANSI_RESET}\r\n"
            return

        verb = tokens[0].lower()

        # Audit log command attempt
        try:
            log_activity(self.user_id, "terminal_command", clean_cmd[:200], self.client_ip)
        except Exception:
            pass

        # 3. Whitelist Dispatcher
        if verb == "help":
            yield self._handle_help()
        elif verb == "clear":
            yield "\x1b[2J\x1b[H"
        elif verb == "docker":
            yield from self._handle_docker(tokens[1:])
        elif verb == "os":
            yield from self._handle_os(tokens[1:])
        elif verb == "demo":
            yield from self._handle_demo(tokens[1:])
        else:
            yield f"{ANSI_RED}Command not recognized: '{verb}'. Type 'help' for available commands.{ANSI_RESET}\r\n"

    def _handle_help(self) -> str:
        """Render help manual table."""
        commands = [
            ["help", "Show this manual of whitelisted commands"],
            ["clear", "Clear terminal screen"],
            ["docker ps", "List running Airutil containers"],
            ["docker stats --no-stream", "Live snapshot of container CPU & memory limits"],
            ["docker inspect <container>", "Show security & cgroup configs (NanoCpus, Memory, CapDrop)"],
            ["docker top <container>", "Inspect isolated processes running inside a container"],
            ["docker network inspect vault_net", "Inspect bridge network IPAM and attached containers"],
            ["docker volume inspect airutil_data", "Inspect shared persistent storage volume metadata"],
            ["docker logs --tail 20 <container>", "View the latest 20 log lines from a container"],
            ["docker images", "List locally built container images"],
            ["docker info", "Summary of Docker Engine runtime and storage driver"],
            ["os ns <app1|app2>", "Inspect live /proc/self/ns symlinks from replica"],
            ["os cgroup <app1|app2>", "Inspect live /sys/fs/cgroup limits from replica"],
            ["os mounts <app1|app2>", "Inspect live /proc/self/mountinfo from replica"],
            ["demo balance", "Send 20 requests to Nginx and print round-robin split"],
            ["demo ttl", "Demonstrate live Redis key expiration countdown"],
            ["demo beam", "Demonstrate atomic single-read burn semantics (GETDEL)"],
        ]
        out = f"{ANSI_BOLD}{ANSI_CYAN}AIRUTIL DOCKER TERMINAL SHOWCASE: WHITELISTED COMMANDS{ANSI_RESET}\r\n\r\n"
        out += format_table(["COMMAND", "DESCRIPTION"], commands)
        out += f"\r\n{ANSI_DIM}Note: All queries run read-only against the Docker Engine API proxy.{ANSI_RESET}\r\n"
        return out

    def _validate_container(self, name: str) -> Optional[str]:
        """Ensure container name is strictly part of the Airutil multi-container suite."""
        normalized = name.strip()
        if not normalized.startswith(ALLOWED_CONTAINER_PREFIX):
            normalized = f"{ALLOWED_CONTAINER_PREFIX}{normalized}"
        if normalized in KNOWN_CONTAINERS:
            return normalized
        return None

    def _handle_docker(self, args: List[str]) -> Generator[str, None, None]:
        """Dispatch whitelisted docker subcommands."""
        if not args:
            yield f"{ANSI_YELLOW}Usage: docker [ps | stats | inspect | top | network | volume | logs | images | info]{ANSI_RESET}\r\n"
            return

        sub = args[0].lower()

        if sub == "ps":
            yield from self._docker_ps()
        elif sub == "stats":
            yield from self._docker_stats()
        elif sub == "inspect":
            if len(args) < 2:
                yield f"{ANSI_YELLOW}Usage: docker inspect <container_name>{ANSI_RESET}\r\n"
                return
            yield from self._docker_inspect(args[1])
        elif sub == "top":
            if len(args) < 2:
                yield f"{ANSI_YELLOW}Usage: docker top <container_name>{ANSI_RESET}\r\n"
                return
            yield from self._docker_top(args[1])
        elif sub == "network":
            if len(args) >= 3 and args[1].lower() == "inspect":
                yield from self._docker_network_inspect(args[2])
            else:
                yield f"{ANSI_YELLOW}Usage: docker network inspect vault_net{ANSI_RESET}\r\n"
        elif sub == "volume":
            if len(args) >= 3 and args[1].lower() == "inspect":
                yield from self._docker_volume_inspect(args[2])
            else:
                yield f"{ANSI_YELLOW}Usage: docker volume inspect airutil_data{ANSI_RESET}\r\n"
        elif sub == "logs":
            target = args[-1]
            tail = 20
            yield from self._docker_logs(target, tail)
        elif sub == "images":
            yield from self._docker_images()
        elif sub == "info":
            yield from self._docker_info()
        else:
            yield f"{ANSI_RED}Subcommand 'docker {sub}' is not permitted in read-only showcase.{ANSI_RESET}\r\n"

    def _docker_ps(self) -> Generator[str, None, None]:
        """Query /containers/json and format output."""
        try:
            resp = requests.get(f"{self.proxy_url}/containers/json", timeout=3)
            if resp.status_code != 200:
                yield f"{ANSI_RED}Failed to query containers: HTTP {resp.status_code}{ANSI_RESET}\r\n"
                return
            containers = resp.json()
            rows = []
            for c in containers:
                c_id = c.get("Id", "")[:12]
                names = ", ".join(n.lstrip("/") for n in c.get("Names", []))
                image = c.get("Image", "")
                status = c.get("Status", "")
                ports = ", ".join(f"{p.get('PublicPort', '')}->{p.get('PrivatePort')}/{p.get('Type')}" for p in c.get("Ports", []) if p.get("PublicPort")) or "None"
                rows.append([c_id, names, image, status, ports])
            yield format_table(["CONTAINER ID", "NAMES", "IMAGE", "STATUS", "PORTS"], rows)
        except Exception as e:
            yield f"{ANSI_RED}Error connecting to Docker proxy: {e}{ANSI_RESET}\r\n"

    def _docker_stats(self) -> Generator[str, None, None]:
        """Query stats for airutil containers."""
        try:
            resp = requests.get(f"{self.proxy_url}/containers/json", timeout=3)
            containers = resp.json() if resp.status_code == 200 else []
            rows = []
            for c in containers:
                name = c.get("Names", [""])[0].lstrip("/")
                if not name.startswith("airutil_"):
                    continue
                # Query stats with stream=false
                s_resp = requests.get(f"{self.proxy_url}/containers/{name}/stats?stream=false", timeout=3)
                if s_resp.status_code == 200:
                    stat = s_resp.json()
                    mem_usage = stat.get("memory_stats", {}).get("usage", 0) / (1024 * 1024)
                    mem_limit = stat.get("memory_stats", {}).get("limit", 0) / (1024 * 1024)
                    mem_pct = round((mem_usage / mem_limit) * 100, 1) if mem_limit else 0
                    rows.append([name, f"{mem_usage:.1f}MiB / {mem_limit:.1f}MiB", f"{mem_pct}%", "Active"])
                else:
                    rows.append([name, "N/A", "N/A", "Up"])
            yield format_table(["CONTAINER", "MEM USAGE / LIMIT", "MEM %", "STATUS"], rows)
        except Exception as e:
            yield f"{ANSI_RED}Error collecting stats: {e}{ANSI_RESET}\r\n"

    def _docker_inspect(self, target: str) -> Generator[str, None, None]:
        """Query /containers/{target}/json and extract curated security attributes."""
        container_name = self._validate_container(target)
        if not container_name:
            yield f"{ANSI_RED}Access denied or container '{target}' not found. Only airutil_ containers are permitted.{ANSI_RESET}\r\n"
            return

        try:
            resp = requests.get(f"{self.proxy_url}/containers/{container_name}/json", timeout=3)
            if resp.status_code != 200:
                yield f"{ANSI_RED}Failed to inspect {container_name}: HTTP {resp.status_code}{ANSI_RESET}\r\n"
                return

            data = resp.json()
            host_cfg = data.get("HostConfig", {})
            curated = {
                "Id": data.get("Id", "")[:12],
                "Name": data.get("Name", "").lstrip("/"),
                "Image": data.get("Config", {}).get("Image", ""),
                "NanoCpus": host_cfg.get("NanoCpus"),
                "Memory": host_cfg.get("Memory"),
                "MemoryReservation": host_cfg.get("MemoryReservation"),
                "ReadonlyRootfs": host_cfg.get("ReadonlyRootfs"),
                "CapDrop": host_cfg.get("CapDrop"),
                "PidsLimit": host_cfg.get("PidsLimit"),
                "SecurityOpt": host_cfg.get("SecurityOpt"),
                "Mounts": [
                    {"Type": m.get("Type"), "Source": m.get("Name") or m.get("Source"), "Destination": m.get("Destination"), "Mode": m.get("Mode"), "RW": m.get("RW")}
                    for m in data.get("Mounts", [])
                ],
                "GraphDriver": data.get("GraphDriver", {}).get("Name"),
            }
            yield json.dumps([curated], indent=2) + "\r\n"
        except Exception as e:
            yield f"{ANSI_RED}Error inspecting container: {e}{ANSI_RESET}\r\n"

    def _docker_top(self, target: str) -> Generator[str, None, None]:
        """Query /containers/{target}/top and print processes."""
        container_name = self._validate_container(target)
        if not container_name:
            yield f"{ANSI_RED}Container '{target}' not found or forbidden.{ANSI_RESET}\r\n"
            return
        try:
            resp = requests.get(f"{self.proxy_url}/containers/{container_name}/top", timeout=3)
            if resp.status_code != 200:
                yield f"{ANSI_RED}Could not query top for {container_name}: HTTP {resp.status_code}{ANSI_RESET}\r\n"
                return
            data = resp.json()
            titles = data.get("Titles", [])
            processes = data.get("Processes", [])
            yield format_table(titles, processes)
        except Exception as e:
            yield f"{ANSI_RED}Error querying processes: {e}{ANSI_RESET}\r\n"

    def _docker_network_inspect(self, net_name: str) -> Generator[str, None, None]:
        """Query /networks/{net_name}."""
        if net_name not in ("vault_net", "showcase_net"):
            yield f"{ANSI_RED}Network '{net_name}' is not in the allowed inspection list.{ANSI_RESET}\r\n"
            return
        try:
            resp = requests.get(f"{self.proxy_url}/networks/{net_name}", timeout=3)
            if resp.status_code != 200:
                yield f"{ANSI_RED}Failed to inspect network: HTTP {resp.status_code}{ANSI_RESET}\r\n"
                return
            data = resp.json()
            curated = {
                "Name": data.get("Name"),
                "Id": data.get("Id", "")[:12],
                "Driver": data.get("Driver"),
                "Scope": data.get("Scope"),
                "IPAM": data.get("IPAM"),
                "Containers": {
                    k[:12]: {"Name": v.get("Name"), "IPv4Address": v.get("IPv4Address")}
                    for k, v in data.get("Containers", {}).items()
                }
            }
            yield json.dumps([curated], indent=2) + "\r\n"
        except Exception as e:
            yield f"{ANSI_RED}Error querying network: {e}{ANSI_RESET}\r\n"

    def _docker_volume_inspect(self, vol_name: str) -> Generator[str, None, None]:
        """Query /volumes/{vol_name}."""
        if vol_name not in ("airutil_data", "airutil_pgdata"):
            yield f"{ANSI_RED}Volume '{vol_name}' is not in the allowed inspection list.{ANSI_RESET}\r\n"
            return
        try:
            resp = requests.get(f"{self.proxy_url}/volumes/{vol_name}", timeout=3)
            if resp.status_code != 200:
                yield f"{ANSI_RED}Failed to inspect volume: HTTP {resp.status_code}{ANSI_RESET}\r\n"
                return
            yield json.dumps(resp.json(), indent=2) + "\r\n"
        except Exception as e:
            yield f"{ANSI_RED}Error querying volume: {e}{ANSI_RESET}\r\n"

    def _docker_logs(self, target: str, tail: int = 20) -> Generator[str, None, None]:
        """Query /containers/{target}/logs."""
        container_name = self._validate_container(target)
        if not container_name:
            yield f"{ANSI_RED}Container '{target}' not found or forbidden.{ANSI_RESET}\r\n"
            return
        try:
            url = f"{self.proxy_url}/containers/{container_name}/logs?stdout=1&stderr=1&tail={tail}"
            resp = requests.get(url, timeout=5)
            if resp.status_code != 200:
                yield f"{ANSI_RED}Failed to fetch logs: HTTP {resp.status_code}{ANSI_RESET}\r\n"
                return
            raw_text = resp.text
            # Remove Docker multiplexing headers if present
            clean_lines = []
            for line in raw_text.splitlines():
                if len(line) > 8 and ord(line[0]) in (1, 2) and line[1:4] == "\x00\x00\x00":
                    clean_lines.append(line[8:])
                else:
                    clean_lines.append(line)
            yield "\r\n".join(clean_lines[-tail:]) + "\r\n"
        except Exception as e:
            yield f"{ANSI_RED}Error fetching container logs: {e}{ANSI_RESET}\r\n"

    def _docker_images(self) -> Generator[str, None, None]:
        """Query /images/json."""
        try:
            resp = requests.get(f"{self.proxy_url}/images/json", timeout=3)
            if resp.status_code != 200:
                yield f"{ANSI_RED}Failed to fetch images: HTTP {resp.status_code}{ANSI_RESET}\r\n"
                return
            images = resp.json()
            rows = []
            for img in images:
                tags = ", ".join(img.get("RepoTags") or ["<none>"])
                img_id = img.get("Id", "")[:19]
                size_mb = round(img.get("Size", 0) / (1024 * 1024), 1)
                rows.append([tags, img_id, f"{size_mb} MB"])
            yield format_table(["REPOSITORY:TAG", "IMAGE ID", "SIZE"], rows)
        except Exception as e:
            yield f"{ANSI_RED}Error fetching images: {e}{ANSI_RESET}\r\n"

    def _docker_info(self) -> Generator[str, None, None]:
        """Query /info."""
        try:
            resp = requests.get(f"{self.proxy_url}/info", timeout=3)
            if resp.status_code != 200:
                yield f"{ANSI_RED}Failed to fetch info: HTTP {resp.status_code}{ANSI_RESET}\r\n"
                return
            data = resp.json()
            rows = [
                ["Operating System", data.get("OperatingSystem", "")],
                ["OSType / Architecture", f"{data.get('OSType')} / {data.get('Architecture')}"],
                ["Kernel Version", data.get("KernelVersion", "")],
                ["Storage Driver", data.get("Driver", "")],
                ["Cgroup Version", data.get("CgroupVersion", "v2")],
                ["Containers (Running)", f"{data.get('ContainersRunning', 0)} / {data.get('Containers', 0)}"],
                ["Server Version", data.get("ServerVersion", "")],
            ]
            yield format_table(["ATTRIBUTE", "VALUE"], rows)
        except Exception as e:
            yield f"{ANSI_RED}Error fetching docker info: {e}{ANSI_RESET}\r\n"

    def _handle_os(self, args: List[str]) -> Generator[str, None, None]:
        """Query internal replica endpoint /internal/oslab for real /proc and /sys values."""
        if len(args) < 2:
            yield f"{ANSI_YELLOW}Usage: os [ns | cgroup | mounts] <app1|app2>{ANSI_RESET}\r\n"
            return

        metric = args[0].lower()
        target = args[1].lower()
        if target not in ("app1", "app2"):
            yield f"{ANSI_RED}Target must be app1 or app2.{ANSI_RESET}\r\n"
            return
        if metric not in ("ns", "cgroup", "mounts"):
            yield f"{ANSI_RED}Metric must be 'ns', 'cgroup', or 'mounts'.{ANSI_RESET}\r\n"
            return

        try:
            url = f"http://{target}:5000/internal/oslab?metric={metric}"
            headers = {"X-Internal-Token": INTERNAL_API_TOKEN}
            resp = requests.get(url, headers=headers, timeout=3)
            if resp.status_code != 200:
                yield f"{ANSI_RED}Failed internal query to {target}: HTTP {resp.status_code}{ANSI_RESET}\r\n"
                return
            data = resp.json()
            yield data.get("output", "No output returned.\r\n")
        except Exception as e:
            yield f"{ANSI_RED}Error communicating with replica {target}: {e}{ANSI_RESET}\r\n"

    def _handle_demo(self, args: List[str]) -> Generator[str, None, None]:
        """Dispatch live educational demo flows."""
        if not args:
            yield f"{ANSI_YELLOW}Usage: demo [balance | ttl | beam]{ANSI_RESET}\r\n"
            return
        action = args[0].lower()

        if action == "balance":
            yield from self._demo_balance()
        elif action == "ttl":
            yield from self._demo_ttl()
        elif action == "beam":
            yield from self._demo_beam()
        else:
            yield f"{ANSI_RED}Unknown demo '{action}'. Available: balance, ttl, beam.{ANSI_RESET}\r\n"

    def _demo_balance(self) -> Generator[str, None, None]:
        """Send 20 requests to Nginx and print round-robin split."""
        yield f"{ANSI_BOLD}{ANSI_CYAN}==> Dispatching 20 HTTP requests to Nginx (port 8080)...{ANSI_RESET}\r\n"
        counts = {"app1": 0, "app2": 0, "other": 0}
        for i in range(1, 21):
            try:
                # Query Nginx gateway internal host or localhost
                r = requests.get("http://nginx:8080/healthz", timeout=2)
                served_by = r.headers.get("X-Served-By", "unknown")
                replica = "app1" if "app1" in served_by else ("app2" if "app2" in served_by else "other")
                counts[replica] += 1
                yield f"  Req #{i:02d}: {r.status_code} OK -> Served by {ANSI_GREEN}{served_by}{ANSI_RESET}\r\n"
            except Exception as e:
                yield f"  Req #{i:02d}: Error: {e}\r\n"
            time.sleep(0.05)

        total = counts["app1"] + counts["app2"]
        p1 = round((counts["app1"] / total) * 100) if total else 0
        p2 = round((counts["app2"] / total) * 100) if total else 0
        yield f"\r\n{ANSI_BOLD}Summary:{ANSI_RESET} app1: {counts['app1']} ({p1}%), app2: {counts['app2']} ({p2}%)\r\n"
        yield f"{ANSI_DIM}What this proves: Nginx round-robin balances layer-7 HTTP load across stateless replicas.{ANSI_RESET}\r\n"

    def _demo_ttl(self) -> Generator[str, None, None]:
        """Create 10s Redis key and stream countdown down to -2."""
        r = get_redis_client()
        key = "showcase:demo_ttl"
        r.setex(key, 10, "VolatileDemoPayload#123")
        yield f"{ANSI_BOLD}{ANSI_CYAN}==> Created Redis key '{key}' with TTL=10s. Streaming countdown:{ANSI_RESET}\r\n"
        for _ in range(12):
            ttl = r.ttl(key)
            val = r.get(key)
            status_str = f"Val: '{val}'" if val else f"{ANSI_RED}(Expired & Purged){ANSI_RESET}"
            yield f"  TTL = {ttl:2d}s | {status_str}\r\n"
            if ttl == -2:
                break
            time.sleep(1.0)
        yield f"{ANSI_DIM}What this proves: Kernel RAM keys are purged automatically upon expiration with zero residual disk footprint.{ANSI_RESET}\r\n"

    def _demo_beam(self) -> Generator[str, None, None]:
        """Demonstrate atomic GETDEL single-read burn semantics."""
        r = get_redis_client()
        token = "demo_token_showcase"
        key = f"beam:{token}"
        payload = json.dumps({"token": token, "content": "BurnOnReadSecretPayload#42", "is_link": False})
        r.setex(key, 60, payload)

        yield f"{ANSI_BOLD}{ANSI_CYAN}==> Created volatile Beam in Redis at key '{key}'{ANSI_RESET}\r\n"
        time.sleep(0.5)

        yield f"--> First Reader executes {ANSI_BOLD}GETDEL {key}{ANSI_RESET}...\r\n"
        first_read = r.getdel(key)
        yield f"    {ANSI_GREEN}✓ Received payload:{ANSI_RESET} {first_read}\r\n"

        time.sleep(0.5)
        yield f"--> Second Reader executes {ANSI_BOLD}GETDEL {key}{ANSI_RESET} immediately after...\r\n"
        second_read = r.getdel(key)
        yield f"    {ANSI_RED}✗ Received payload:{ANSI_RESET} {second_read} (Atomically destroyed on first read)\r\n"
        yield f"{ANSI_DIM}What this proves: Redis GETDEL provides race-free, atomic burn-on-read single consumer delivery.{ANSI_RESET}\r\n"
