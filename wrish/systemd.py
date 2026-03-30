from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
import subprocess


def _prompt(text: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{text}{suffix}: ").strip()
    return value or (default or "")


def _prompt_bool(text: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    value = input(f"{text} [{hint}]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "1", "true"}


@dataclass(slots=True)
class SystemdConfig:
    service_name: str
    description: str
    command: list[str]


def _build_execstart(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def render_service(config: SystemdConfig) -> str:
    execstart = _build_execstart(config.command)
    return "\n".join(
        [
            "[Unit]",
            f"Description={config.description}",
            "After=network-online.target bluetooth.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            "WorkingDirectory=%h",
            "EnvironmentFile=-%h/.wrishrc",
            f"ExecStart={execstart}",
            "Restart=always",
            "RestartSec=5",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def run_systemd_wizard(default_binary: str, *, force_install: bool = False) -> Path:
    service_path = Path.home() / ".config/systemd/user/wrish.service"
    if service_path.exists() and not force_install:
        _show_existing_service_info(service_path)
        if not _prompt_bool("Reinstall this service", False):
            print("Nothing changed.")
            raise SystemExit(0)

    print("wrish systemd wizard")
    print("This creates a user-level systemd service in ~/.config/systemd/user.")
    print("")

    service_name = _prompt("Service name", "wrish")
    description = _prompt("Description", "wrish bracelet service")
    use_relay = _prompt_bool("Enable relay mode", True)
    use_sentinel = _prompt_bool("Enable sentinel monitoring", True)
    debug = _prompt_bool("Enable --debug logs", False)

    command = [default_binary]
    if debug:
        command.append("--debug")

    if use_relay:
        relay_url = _prompt("Relay URL (.relay)", "https://www.hookpool.com/braccialetto/7bgs3p.relay")
        bind = _prompt("Relay bind address", "127.0.0.1")
        port = _prompt("Relay bind port", "8787")
        command.extend(["relay", relay_url, "--bind", bind, "--port", port])
        if use_sentinel:
            command.append("--sentinel")
    else:
        command.append("sentinel")

    service = render_service(
        SystemdConfig(
            service_name=service_name,
            description=description,
            command=command,
        )
    )

    target_dir = Path.home() / ".config/systemd/user"
    target_dir.mkdir(parents=True, exist_ok=True)
    service_path = target_dir / f"{service_name}.service"
    service_path.write_text(service, encoding="utf-8")

    print("")
    print(f"Written: {service_path}")

    _run_systemctl(["systemctl", "--user", "daemon-reload"])
    _run_systemctl(["systemctl", "--user", "enable", "--now", f"{service_name}.service"])
    _run_systemctl(["systemctl", "--user", "restart", f"{service_name}.service"])
    _run_systemctl(["systemctl", "--user", "status", f"{service_name}.service"])

    if _prompt_bool("Follow live logs now", False):
        subprocess.run(["journalctl", "--user", "-u", f"{service_name}.service", "-f"], check=False)

    return service_path


def _run_systemctl(command: list[str]) -> None:
    print("")
    print("$", _build_execstart(command))
    completed = subprocess.run(command, check=False, text=True)
    if completed.returncode != 0:
        print(f"Command failed with exit code {completed.returncode}")


def _show_existing_service_info(service_path: Path) -> None:
    print("Existing wrish systemd service detected.")
    print(f"Service file: {service_path}")

    execstart = ""
    description = ""
    for line in service_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Description="):
            description = line.partition("=")[2]
        elif line.startswith("ExecStart="):
            execstart = line.partition("=")[2]

    if description:
        print(f"Description: {description}")
    if execstart:
        print(f"ExecStart: {execstart}")

    service_name = service_path.name
    active = _read_systemctl_output(["systemctl", "--user", "is-active", service_name])
    enabled = _read_systemctl_output(["systemctl", "--user", "is-enabled", service_name])
    if active:
        print(f"Active: {active}")
    if enabled:
        print(f"Enabled: {enabled}")
    print("")
    print("Use `wrish systemd --install` to force a reinstall immediately.")


def _read_systemctl_output(command: list[str]) -> str:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    output = (completed.stdout or completed.stderr).strip()
    return output


def follow_logs(service_name: str = "wrish.service") -> int:
    return subprocess.run(
        ["journalctl", "--user", "-u", service_name, "-f"],
        check=False,
    ).returncode
