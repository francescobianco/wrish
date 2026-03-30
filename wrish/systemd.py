from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex


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
            f"ExecStart={execstart}",
            "Restart=always",
            "RestartSec=5",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def run_systemd_wizard(default_binary: str) -> Path:
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
    print("")
    print("Next commands:")
    print("  systemctl --user daemon-reload")
    print(f"  systemctl --user enable --now {service_name}.service")
    print(f"  systemctl --user status {service_name}.service")
    print(f"  journalctl --user -u {service_name}.service -f")

    return service_path
