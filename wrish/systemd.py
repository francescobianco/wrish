from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
import subprocess

from .config import load_config


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


def _service_label(service_name: str) -> str:
    return service_name[:-8] if service_name.endswith(".service") else service_name


def _format_systemctl_success(verb: str, service_name: str | None = None) -> str:
    label = _service_label(service_name) if service_name else "wrish"
    messages = {
        "start": f"systemd: {label} started",
        "stop": f"systemd: {label} stopped",
        "restart": f"systemd: {label} restarted",
        "enable": f"systemd: {label} enabled",
        "disable": f"systemd: {label} disabled",
        "daemon-reload": "systemd: daemon reloaded",
        "status": f"systemd: showing status for {label}",
    }
    return messages.get(verb, f"systemd: {' '.join(part for part in (label, verb) if part)}")


def _run_systemctl(command: list[str]) -> int:
    verb = command[2] if len(command) >= 3 else "systemctl"
    service_name = command[-1] if command and command[-1].endswith(".service") else None
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode == 0:
        print(_format_systemctl_success(verb, service_name))
    else:
        print(f"systemd: {verb} failed for {_service_label(service_name or 'wrish.service')} (exit {completed.returncode})")
        error_output = (completed.stderr or completed.stdout).strip()
        if error_output:
            print(error_output)
    return completed.returncode


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

    cfg = load_config()

    service_name = _prompt("Service name", "wrish")
    description = _prompt("Description", "wrish bracelet service")
    mac = _prompt("Device MAC address", cfg.mac)
    hci = _prompt("Bluetooth adapter", cfg.hci)
    use_relay = _prompt_bool("Enable relay mode", True)
    use_sentinel = _prompt_bool("Enable sentinel monitoring", True)
    debug = _prompt_bool("Enable --debug logs", False)

    command = [default_binary, "--mac", mac, "--hci", hci]
    if debug:
        command.append("--debug")

    if use_relay:
        relay_url = _prompt("Relay URL (.relay)", "https://www.hookpool.com/xxxx/xxxx.relay")
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
    _run_systemctl(["systemctl", "--user", "enable", f"{service_name}.service"])
    _run_systemctl(["systemctl", "--user", "restart", f"{service_name}.service"])
    _run_systemctl(["systemctl", "--user", "status", f"{service_name}.service"])

    if _prompt_bool("Follow live logs now", False):
        subprocess.run(["journalctl", "--user", "-u", f"{service_name}.service", "-f"], check=False)

    return service_path


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


def _default_service_path() -> Path:
    return Path.home() / ".config/systemd/user/wrish.service"


def resolve_service_name(default: str = "wrish.service") -> str:
    service_path = _default_service_path()
    if service_path.exists():
        return service_path.name
    return default


def systemd_action(action: str, service_name: str | None = None) -> int:
    resolved_service = service_name or resolve_service_name()
    if not resolved_service.endswith(".service"):
        resolved_service = f"{resolved_service}.service"

    commands: dict[str, list[list[str]]] = {
        "start": [["systemctl", "--user", "start", resolved_service]],
        "stop": [["systemctl", "--user", "stop", resolved_service]],
        "reset": [
            ["systemctl", "--user", "stop", resolved_service],
            ["systemctl", "--user", "disable", resolved_service],
            ["systemctl", "--user", "daemon-reload"],
        ],
    }
    if action not in commands:
        raise ValueError(f"Unsupported systemd action: {action}")

    exit_code = 0
    for command in commands[action]:
        exit_code = _run_systemctl(command) or exit_code

    if action == "reset":
        service_path = _default_service_path()
        if service_path.exists():
            service_path.unlink()
            print(f"systemd: removed {service_path}")

    return exit_code


def follow_logs(service_name: str = "wrish.service") -> int:
    return subprocess.run(
        ["journalctl", "--user", "-u", service_name, "-f"],
        check=False,
    ).returncode
