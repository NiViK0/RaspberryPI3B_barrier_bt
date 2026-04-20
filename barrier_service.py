#!/usr/bin/env python3
import argparse
import logging
import sys
import time
from dataclasses import replace

from serial import SerialException

from barrier_bluetooth import BluetoothCtlSession, scan_once
from barrier_config import Config, load_config
from barrier_db import (
    add_device,
    backup_db,
    get_enabled_macs,
    init_db,
    list_devices,
    log_event,
    normalize_mac,
    remove_device,
    set_device_enabled,
)
from barrier_presence import detect_any_target_presence, process_presence, validate_mac
from barrier_relay import RelayController, detect_relay_port
from barrier_types import PresenceStatus, State


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


def cmd_init_db(config: Config) -> None:
    init_db(config.db_path)
    log_db_event(config, "INFO", "cli", "init-db", f"База инициализирована: {config.db_path}")
    print(f"База инициализирована: {config.db_path}")


def cmd_add(config: Config, mac: str, name: str) -> None:
    init_db(config.db_path)
    normalized_mac = normalize_mac(mac)
    if not validate_mac(normalized_mac):
        raise ValueError(f"Некорректный MAC: {normalized_mac}")

    add_device(config.db_path, normalized_mac, name)
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


def cmd_test_open(config: Config) -> None:
    init_db(config.db_path)
    with RelayController(config) as relay:
        relay.pulse()
    log_db_event(config, "INFO", "cli", "relay-test", "Тестовый импульс на реле отправлен")
    print("Тестовый импульс на реле отправлен")


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


def cmd_run(config: Config) -> None:
    init_db(config.db_path)
    state = State()
    bt = BluetoothCtlSession()

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
        return True

    try:
        allowed_macs = get_enabled_macs(config.db_path)
        if not allowed_macs:
            logging.error("В базе нет разрешённых MAC-адресов")
            log_db_event(config, "ERROR", "service", "empty-allow-list", "В базе нет разрешённых MAC-адресов")
            sys.exit(1)

        logging.info("Разрешённых MAC-адресов: %s", len(allowed_macs))
        log_db_event(config, "INFO", "service", "service-start", "BLE-сервис запущен")
        bt.start()

        with RelayController(config) as relay:
            while True:
                allowed_macs = get_enabled_macs(config.db_path)
                if not allowed_macs:
                    logging.warning("Список разрешённых MAC пуст")
                    time.sleep(config.check_interval)
                    continue

                base_status, devices_output = scan_once(bt, config.scan_time)

                if base_status == PresenceStatus.SCAN_FAILED:
                    log_db_event(config, "WARN", "service", "scan-failed", "BLE-сканирование не удалось")
                    process_presence(base_status, devices_output, config, state, trigger_action)
                else:
                    actual_presence = detect_any_target_presence(devices_output, allowed_macs)
                    process_presence(actual_presence, devices_output, config, state, trigger_action)

                time.sleep(config.check_interval)

    except KeyboardInterrupt:
        logging.info("Остановлено пользователем")
        log_db_event(config, "INFO", "service", "service-stop", "Остановлено пользователем")
    except SerialException:
        logging.exception("Ошибка доступа к порту реле: %s", config.relay_port)
        log_db_event(config, "ERROR", "service", "relay-open-error", f"Ошибка доступа к порту реле: {config.relay_port}")
        sys.exit(1)
    except Exception:
        logging.exception("Критическая ошибка")
        log_db_event(config, "ERROR", "service", "critical-error", "Критическая ошибка BLE-сервиса")
        sys.exit(1)
    finally:
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
    p_add.set_defaults(handler=lambda config, args: cmd_add(config, args.mac, args.name))

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

    p_detect_relay = subparsers.add_parser("detect-relay", help="Найти serial-порт реле")
    p_detect_relay.set_defaults(handler=lambda config, args: cmd_detect_relay(config))

    p_backup_db = subparsers.add_parser("backup-db", help="Сделать backup SQLite-базы")
    p_backup_db.set_defaults(handler=lambda config, args: cmd_backup_db(config))

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
