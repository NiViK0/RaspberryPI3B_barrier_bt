#!/usr/bin/env python3
import argparse
import logging
import sys
import time
from dataclasses import replace

from barrier_bluetooth import (
    BluetoothCtlSession,
    collect_scan_details,
    detect_allowed_presence_from_details,
    scan_once,
)
from barrier_config import Config, load_config
from barrier_db import (
    add_device,
    backup_db,
    get_enabled_device_map,
    get_enabled_macs,
    init_db,
    list_devices,
    log_open_event,
    log_event,
    normalize_mac,
    remove_device,
    save_bluetooth_status,
    save_wifi_status,
    set_device_enabled,
)
from barrier_presence import process_presence, validate_mac
from barrier_relay import RelayController, SerialDependencyError, SerialException, detect_relay_port
from barrier_types import PresenceStatus, State
from barrier_wifi import collect_wifi_details, format_wifi_devices_output


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def log_db_event(config: Config, level: str, source: str, action: str, message: str) -> None:
    try:
        log_event(config.db_path, level, source, action, message)
    except Exception:
        logging.debug("Не удалось записать событие в БД", exc_info=True)


def _save_ble_status(
    config: Config,
    status: PresenceStatus,
    devices_output: str,
    details: dict[str, object] | None,
    presence: PresenceStatus,
    missing_count: int = 0,
) -> None:
    """Persist BLE scan result to the database (single row upsert)."""
    if status == PresenceStatus.SCAN_FAILED or details is None:
        save_bluetooth_status(
            config.db_path,
            "scan_failed",
            0, 0, 0,
            None, "", [],
            devices_output,
            "BLE scan failed",
            presence_status=PresenceStatus.SCAN_FAILED.name.lower(),
            missing_count=missing_count,
            missing_threshold=config.missing_threshold,
            min_rssi=config.min_rssi,
            allowed_present=False,
        )
        return

    save_bluetooth_status(
        config.db_path,
        "ok",
        int(details["total_devices"]),
        int(details["connected_devices"]),
        int(details["allowed_seen"]),
        details["max_rssi"],  # type: ignore[arg-type]
        str(details["strongest_device"]),
        details["devices"],  # type: ignore[arg-type]
        devices_output,
        presence_status=presence.name.lower(),
        missing_count=missing_count,
        missing_threshold=config.missing_threshold,
        min_rssi=config.min_rssi,
        allowed_present=presence == PresenceStatus.PRESENT,
    )


def save_scan_status(
    config: Config,
    status: PresenceStatus,
    devices_output: str,
    allowed_macs: list[str],
    bt: BluetoothCtlSession,
) -> dict[str, object] | None:
    """Used by cmd_scan_status: collect details then persist."""
    try:
        details: dict[str, object] | None = None
        presence = PresenceStatus.SCAN_FAILED
        if status != PresenceStatus.SCAN_FAILED:
            details = collect_scan_details(bt, devices_output, allowed_macs)
            presence = detect_allowed_presence_from_details(
                details["devices"],  # type: ignore[arg-type]
                config.min_rssi,
            )
        _save_ble_status(config, status, devices_output, details, presence)
        return details
    except Exception:
        logging.exception("Could not save BLE status")
        return None


def save_scan_snapshot(
    config: Config,
    status: PresenceStatus,
    devices_output: str,
    details: dict[str, object] | None,
    state: State,
    presence: PresenceStatus,
) -> None:
    """Used by the main loop: details already collected, persist with state."""
    _save_ble_status(config, status, devices_output, details, presence, missing_count=state.missing_count)


def save_wifi_snapshot(
    config: Config,
    status: PresenceStatus,
    details: dict[str, object] | None,
    state: State,
    error: str = "",
) -> None:
    if status == PresenceStatus.SCAN_FAILED or details is None:
        save_wifi_status(
            config.db_path,
            "scan_failed",
            config.wifi_interface,
            0,
            0,
            None,
            "",
            [],
            "",
            error or "Wi-Fi scan failed",
            presence_status=PresenceStatus.SCAN_FAILED.name.lower(),
            missing_count=state.missing_count,
            missing_threshold=config.missing_threshold,
            min_signal=config.wifi_min_signal,
            max_inactive_ms=config.wifi_max_inactive_ms,
            allowed_present=False,
        )
        return

    save_wifi_status(
        config.db_path,
        "ok",
        config.wifi_interface,
        int(details["connected_devices"]),
        int(details["allowed_seen"]),
        details["max_signal"],  # type: ignore[arg-type]
        str(details["strongest_station"]),
        details["stations"],  # type: ignore[arg-type]
        str(details["raw_output"]),
        "",
        presence_status=status.name.lower(),
        missing_count=state.missing_count,
        missing_threshold=config.missing_threshold,
        min_signal=config.wifi_min_signal,
        max_inactive_ms=config.wifi_max_inactive_ms,
        allowed_present=status == PresenceStatus.PRESENT,
    )


def combine_presence_statuses(statuses: list[PresenceStatus]) -> PresenceStatus:
    if not statuses:
        return PresenceStatus.SCAN_FAILED
    if PresenceStatus.PRESENT in statuses:
        return PresenceStatus.PRESENT
    if any(status != PresenceStatus.SCAN_FAILED for status in statuses):
        return PresenceStatus.ABSENT
    return PresenceStatus.SCAN_FAILED


def select_trigger_context(
    wifi_details: dict[str, object] | None,
    ble_details: dict[str, object] | None,
    enabled_devices: dict[str, dict[str, object]],
) -> dict[str, object] | None:
    if wifi_details:
        for station in wifi_details.get("stations", []):  # type: ignore[union-attr]
            if not isinstance(station, dict) or not station.get("allowed") or not station.get("active"):
                continue
            mac = normalize_mac(str(station.get("mac", "")))
            device = enabled_devices.get(mac, {})
            return {
                "source": "wifi",
                "mac": mac,
                "signal": station.get("signal"),
                "device_name": device.get("name") or station.get("name") or mac,
                "profile_name": device.get("profile_name") or device.get("name") or station.get("name") or mac,
            }

    if ble_details:
        for device_seen in ble_details.get("devices", []):  # type: ignore[union-attr]
            if not isinstance(device_seen, dict) or not device_seen.get("allowed"):
                continue
            mac = normalize_mac(str(device_seen.get("mac", "")))
            device = enabled_devices.get(mac, {})
            return {
                "source": "ble",
                "mac": mac,
                "signal": device_seen.get("rssi"),
                "device_name": device.get("name") or device_seen.get("name") or mac,
                "profile_name": device.get("profile_name") or device.get("name") or device_seen.get("name") or mac,
            }

    return None


def cmd_init_db(config: Config) -> None:
    init_db(config.db_path)
    log_db_event(config, "INFO", "cli", "init-db", f"База инициализирована: {config.db_path}")
    print(f"База инициализирована: {config.db_path}")


def cmd_add(config: Config, mac: str, name: str, device_type: str = "unknown", profile_name: str | None = None) -> None:
    init_db(config.db_path)
    normalized_mac = normalize_mac(mac)
    if not validate_mac(normalized_mac):
        raise ValueError(f"Некорректный MAC: {normalized_mac}")

    add_device(config.db_path, normalized_mac, name, device_type, profile_name)
    log_db_event(config, "INFO", "cli", "device-add", f"Добавлено: {name} [{normalized_mac}]")
    print(f"Добавлено: {name} [{normalized_mac}]")


def cmd_list(config: Config) -> None:
    init_db(config.db_path)
    rows = list_devices(config.db_path)
    if not rows:
        print("База пуста")
        return

    for row_id, name, mac, enabled in rows:
        status = "enabled" if enabled else "disabled"
        print(f"{row_id}: {name} | {mac} | {status}")


def cmd_enable(config: Config, mac: str) -> None:
    init_db(config.db_path)
    normalized_mac = normalize_mac(mac)
    if set_device_enabled(config.db_path, normalized_mac, True):
        log_db_event(config, "INFO", "cli", "device-enable", f"Устройство включено: {normalized_mac}")
        print(f"Устройство включено: {normalized_mac}")
    else:
        print(f"Устройство не найдено: {normalized_mac}")
        sys.exit(1)


def cmd_disable(config: Config, mac: str) -> None:
    init_db(config.db_path)
    normalized_mac = normalize_mac(mac)
    if set_device_enabled(config.db_path, normalized_mac, False):
        log_db_event(config, "INFO", "cli", "device-disable", f"Устройство отключено: {normalized_mac}")
        print(f"Устройство отключено: {normalized_mac}")
    else:
        print(f"Устройство не найдено: {normalized_mac}")
        sys.exit(1)


def cmd_remove(config: Config, mac: str) -> None:
    init_db(config.db_path)
    normalized_mac = normalize_mac(mac)
    if remove_device(config.db_path, normalized_mac):
        log_db_event(config, "INFO", "cli", "device-remove", f"Устройство удалено: {normalized_mac}")
        print(f"Устройство удалено: {normalized_mac}")
    else:
        print(f"Устройство не найдено: {normalized_mac}")
        sys.exit(1)


def pulse_relay_once(config: Config) -> None:
    init_db(config.db_path)
    try:
        with RelayController(config) as relay:
            relay.pulse()
    except SerialDependencyError as exc:
        print(exc)
        sys.exit(1)


def cmd_test_open(config: Config) -> None:
    pulse_relay_once(config)
    log_open_event(config.db_path, "test", "", "relay-test", "", None, "opened", "test-open command")
    log_db_event(config, "INFO", "cli", "relay-test", "Тестовый импульс на реле отправлен")
    print("Тестовый импульс на реле отправлен")


def cmd_manual_open(config: Config) -> None:
    pulse_relay_once(config)
    log_open_event(config.db_path, "manual", "", "manual-open", "", None, "opened", "manual-open command")
    log_db_event(config, "INFO", "cli", "manual-open", "Шлагбаум открыт вручную")
    print("Шлагбаум открыт вручную")


def cmd_emergency_open(config: Config) -> None:
    pulse_relay_once(config)
    log_open_event(config.db_path, "emergency", "", "emergency-open", "", None, "opened", "emergency-open command")
    log_db_event(config, "WARN", "cli", "emergency-open", "Аварийное открытие шлагбаума")
    print("Аварийное открытие шлагбаума выполнено")


def cmd_detect_relay(config: Config) -> None:
    port = detect_relay_port()
    if port is None:
        print("Serial-порт реле не найден")
        sys.exit(1)
    print(port)


def cmd_backup_db(config: Config) -> None:
    init_db(config.db_path)
    backup_path = backup_db(config.db_path, config.backup_dir)
    log_db_event(config, "INFO", "cli", "backup-db", f"Backup базы создан: {backup_path}")
    print(f"Backup базы создан: {backup_path}")


def cmd_scan_status(config: Config) -> None:
    init_db(config.db_path)
    allowed_macs = get_enabled_macs(config.db_path)
    bt = BluetoothCtlSession()
    try:
        base_status, devices_output = scan_once(bt, config.scan_time)
        details = None
        presence = PresenceStatus.SCAN_FAILED
        if base_status != PresenceStatus.SCAN_FAILED:
            details = collect_scan_details(bt, devices_output, allowed_macs)
            presence = detect_allowed_presence_from_details(
                details["devices"],  # type: ignore[arg-type]
                config.min_rssi,
            )

        save_scan_snapshot(config, base_status, devices_output, details, State(), presence)
        if base_status == PresenceStatus.SCAN_FAILED:
            print("BLE scan failed")
        else:
            assert details is not None
            print(
                "BLE scan saved: "
                f"devices={details['total_devices']} "
                f"connected={details['connected_devices']} "
                f"allowed_seen={details['allowed_seen']} "
                f"presence={presence.name.lower()}"
            )
    finally:
        bt.stop()


def cmd_wifi_status(config: Config) -> None:
    init_db(config.db_path)
    allowed_macs = get_enabled_macs(config.db_path)
    try:
        details = collect_wifi_details(config, allowed_macs)
        presence = details["presence"]  # type: ignore[assignment]
        assert isinstance(presence, PresenceStatus)
        save_wifi_snapshot(config, presence, details, State())
        print(
            "Wi-Fi scan saved: "
            f"interface={config.wifi_interface} "
            f"connected={details['connected_devices']} "
            f"allowed_seen={details['allowed_seen']} "
            f"presence={presence.name.lower()}"
        )
    except Exception as exc:
        save_wifi_snapshot(config, PresenceStatus.SCAN_FAILED, None, State(), str(exc))
        print(f"Wi-Fi scan failed: {exc}")
        sys.exit(1)


def cmd_run(config: Config) -> None:
    init_db(config.db_path)
    state = State()
    bt = BluetoothCtlSession() if config.bluetooth_enabled else None
    trigger_context: dict[str, object] | None = None

    def trigger_action(action: str) -> bool:
        try:
            relay.pulse()
        except SerialException:
            logging.exception("Ошибка работы с реле")
            log_db_event(config, "ERROR", "service", "relay-error", "Ошибка работы с реле")
            return False
        except Exception:
            logging.exception("Неожиданная ошибка при работе с реле")
            log_db_event(config, "ERROR", "service", "relay-error", "Неожиданная ошибка при работе с реле")
            return False

        log_db_event(config, "INFO", "service", f"barrier-{action}", f"Импульс реле: {action}")
        context = trigger_context or {}
        if action == "open":
            log_open_event(
                config.db_path,
                str(context.get("source") or "unknown"),
                str(context.get("profile_name") or ""),
                str(context.get("device_name") or ""),
                str(context.get("mac") or ""),
                int(context["signal"]) if context.get("signal") is not None else None,
                "opened",
                f"auto-open via {context.get('source') or 'unknown'}",
            )
        return True

    try:
        if not config.bluetooth_enabled and not config.wifi_auto_open:
            logging.error("Нет включённых источников обнаружения: включи BLE или BARRIER_WIFI_AUTO_OPEN=true")
            log_db_event(config, "ERROR", "service", "no-presence-sources", "Нет включённых источников обнаружения")
            sys.exit(1)

        allowed_macs = get_enabled_macs(config.db_path)
        logging.info("Разрешённых MAC-адресов: %s", len(allowed_macs))
        if not allowed_macs:
            logging.warning("Список разрешённых MAC пуст, сервис будет ждать добавления устройства")
            log_db_event(
                config,
                "WARN",
                "service",
                "empty-allow-list",
                "Список разрешённых MAC пуст, сервис ждёт добавления устройства",
            )

        sources = []
        if config.bluetooth_enabled:
            sources.append("BLE")
        if config.wifi_auto_open:
            sources.append(f"Wi-Fi:{config.wifi_interface}")

        log_db_event(config, "INFO", "service", "service-start", f"Сервис запущен, источники: {', '.join(sources)}")
        if bt is not None:
            try:
                bt.start()
            except Exception as exc:
                if not config.wifi_auto_open:
                    raise
                logging.warning("BLE недоступен, продолжаю работу только с Wi-Fi: %s", exc)
                log_db_event(config, "WARN", "service", "ble-start-failed", f"BLE недоступен, Wi-Fi режим продолжает работу: {exc}")
                bt.stop()
                bt = None

        with RelayController(config) as relay:
            while True:
                allowed_macs = get_enabled_macs(config.db_path)
                enabled_devices = get_enabled_device_map(config.db_path)

                statuses: list[PresenceStatus] = []
                combined_outputs: list[str] = []

                ble_base_status = PresenceStatus.SCAN_FAILED
                ble_presence = PresenceStatus.SCAN_FAILED
                ble_devices_output = ""
                ble_details: dict[str, object] | None = None

                if bt is not None:
                    ble_base_status, ble_devices_output = scan_once(bt, config.scan_time)
                    if ble_base_status == PresenceStatus.SCAN_FAILED:
                        log_db_event(config, "WARN", "service", "scan-failed", "BLE-сканирование не удалось")
                        ble_presence = PresenceStatus.SCAN_FAILED
                    else:
                        if not allowed_macs:
                            logging.warning("Список разрешённых MAC пуст, BLE-статус сохранён только для диагностики")
                        ble_details = collect_scan_details(bt, ble_devices_output, allowed_macs)
                        ble_presence = (
                            detect_allowed_presence_from_details(
                                ble_details["devices"],  # type: ignore[arg-type]
                                config.min_rssi,
                            )
                            if allowed_macs
                            else PresenceStatus.ABSENT
                        )

                    statuses.append(ble_presence)
                    combined_outputs.append("BLE:\n" + (ble_devices_output or "BLE scan failed"))

                wifi_presence = PresenceStatus.SCAN_FAILED
                wifi_details: dict[str, object] | None = None
                wifi_error = ""

                if config.wifi_auto_open:
                    try:
                        wifi_details = collect_wifi_details(config, allowed_macs)
                        wifi_presence = wifi_details["presence"]  # type: ignore[assignment]
                        assert isinstance(wifi_presence, PresenceStatus)
                        wifi_output = format_wifi_devices_output(
                            wifi_details["stations"]  # type: ignore[arg-type]
                        )
                        combined_outputs.append("Wi-Fi:\n" + (wifi_output or "Wi-Fi stations not found"))
                    except Exception as exc:
                        wifi_error = str(exc)
                        logging.warning("Ошибка Wi-Fi-сканирования: %s", exc)
                        log_db_event(config, "WARN", "service", "wifi-scan-failed", wifi_error)
                        combined_outputs.append(f"Wi-Fi scan failed: {wifi_error}")

                    statuses.append(wifi_presence)

                combined_presence = combine_presence_statuses(statuses)
                trigger_context = select_trigger_context(wifi_details, ble_details, enabled_devices)
                process_presence(
                    combined_presence,
                    "\n\n".join(combined_outputs),
                    config,
                    state,
                    trigger_action,
                )

                if bt is not None:
                    save_scan_snapshot(config, ble_base_status, ble_devices_output, ble_details, state, ble_presence)

                if config.wifi_auto_open:
                    save_wifi_snapshot(config, wifi_presence, wifi_details, state, wifi_error)

                time.sleep(config.check_interval)

    except KeyboardInterrupt:
        logging.info("Остановлено пользователем")
        log_db_event(config, "INFO", "service", "service-stop", "Остановлено пользователем")
    except SerialException:
        logging.exception("Ошибка доступа к порту реле: %s", config.relay_port)
        log_db_event(config, "ERROR", "service", "relay-open-error", f"Ошибка доступа к порту реле: {config.relay_port}")
        sys.exit(1)
    except SerialDependencyError as exc:
        logging.error("%s", exc)
        log_db_event(config, "ERROR", "service", "serial-dependency-error", str(exc))
        sys.exit(1)
    except Exception:
        logging.exception("Критическая ошибка")
        log_db_event(config, "ERROR", "service", "critical-error", "Критическая ошибка сервиса")
        sys.exit(1)
    finally:
        if bt is not None:
            bt.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Barrier BLE controller")
    parser.add_argument("--dry-run", action="store_true", help="Не активировать реле, только логировать")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_init_db = subparsers.add_parser("init-db", help="Создать SQLite-базу")
    p_init_db.set_defaults(handler=lambda config, args: cmd_init_db(config))

    p_add = subparsers.add_parser("add", help="Добавить или обновить устройство")
    p_add.add_argument("mac", help="MAC-адрес телефона")
    p_add.add_argument("name", help="Имя устройства")
    p_add.add_argument("--type", default="unknown", choices=["wifi", "ble", "unknown"], help="MAC type: wifi/ble/unknown")
    p_add.add_argument("--profile", default=None, help="Phone/profile name")
    p_add.set_defaults(handler=lambda config, args: cmd_add(config, args.mac, args.name, args.type, args.profile))

    p_enable = subparsers.add_parser("enable", help="Включить устройство")
    p_enable.add_argument("mac", help="MAC-адрес устройства")
    p_enable.set_defaults(handler=lambda config, args: cmd_enable(config, args.mac))

    p_disable = subparsers.add_parser("disable", help="Отключить устройство")
    p_disable.add_argument("mac", help="MAC-адрес устройства")
    p_disable.set_defaults(handler=lambda config, args: cmd_disable(config, args.mac))

    p_remove = subparsers.add_parser("remove", help="Удалить устройство")
    p_remove.add_argument("mac", help="MAC-адрес устройства")
    p_remove.set_defaults(handler=lambda config, args: cmd_remove(config, args.mac))

    p_list = subparsers.add_parser("list", help="Показать устройства")
    p_list.set_defaults(handler=lambda config, args: cmd_list(config))

    p_test_open = subparsers.add_parser("test-open", help="Тестовый импульс на реле")
    p_test_open.set_defaults(handler=lambda config, args: cmd_test_open(config))

    p_manual_open = subparsers.add_parser("manual-open", help="Открыть шлагбаум вручную")
    p_manual_open.set_defaults(handler=lambda config, args: cmd_manual_open(config))

    p_emergency_open = subparsers.add_parser("emergency-open", help="Аварийно открыть шлагбаум")
    p_emergency_open.set_defaults(handler=lambda config, args: cmd_emergency_open(config))

    p_detect_relay = subparsers.add_parser("detect-relay", help="Найти serial-порт реле")
    p_detect_relay.set_defaults(handler=lambda config, args: cmd_detect_relay(config))

    p_backup_db = subparsers.add_parser("backup-db", help="Сделать backup SQLite-базы")
    p_backup_db.set_defaults(handler=lambda config, args: cmd_backup_db(config))

    p_scan_status = subparsers.add_parser("scan-status", help="Обновить BLE-статус для web-панели")
    p_scan_status.set_defaults(handler=lambda config, args: cmd_scan_status(config))

    p_wifi_status = subparsers.add_parser("wifi-status", help="Обновить Wi-Fi-статус для web-панели")
    p_wifi_status.set_defaults(handler=lambda config, args: cmd_wifi_status(config))

    p_run = subparsers.add_parser("run", help="Запустить основной цикл")
    p_run.set_defaults(handler=lambda config, args: cmd_run(config))

    return parser


def main() -> None:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args()

    config = load_config()
    if args.dry_run:
        config = replace(config, dry_run=True)

    args.handler(config, args)


if __name__ == "__main__":
    main()
