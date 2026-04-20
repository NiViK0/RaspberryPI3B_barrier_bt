import logging
import re
import time
from collections.abc import Callable

from barrier_config import Config
from barrier_db import normalize_mac
from barrier_types import PresenceStatus, State

TriggerAction = Callable[[str], bool]


def validate_mac(mac: str) -> bool:
    return bool(re.fullmatch(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", normalize_mac(mac)))


def detect_any_target_presence(devices_output: str, allowed_macs: list[str]) -> PresenceStatus:
    devices_upper = devices_output.upper()
    for mac in allowed_macs:
        if normalize_mac(mac) in devices_upper:
            logging.info("Обнаружен разрешённый MAC: %s", normalize_mac(mac))
            return PresenceStatus.PRESENT
    return PresenceStatus.ABSENT


def trigger_barrier(config: Config, state: State, action: str, trigger_action: TriggerAction) -> bool:
    now = time.monotonic()
    if now - state.last_trigger_monotonic < config.cooldown:
        logging.info("Импульс '%s' заблокирован cooldown", action)
        return False

    if action == "open":
        logging.info(">>> Разрешённый телефон найден, открываем шлагбаум")
    elif action == "close":
        logging.info("<<< Телефон исчез, закрываем шлагбаум")
    else:
        logging.info("*** Выполняем действие: %s", action)

    if not trigger_action(action):
        return False

    state.last_trigger_monotonic = now
    return True


def process_presence(
    presence: PresenceStatus,
    devices_output: str,
    config: Config,
    state: State,
    trigger_action: TriggerAction,
) -> None:
    if devices_output.strip():
        logging.info("Найденные устройства:\n%s", devices_output)
    else:
        logging.info("Список устройств пуст")

    if presence == PresenceStatus.SCAN_FAILED:
        logging.warning("Сканирование не удалось, состояние не меняем")
        return

    if presence == PresenceStatus.PRESENT:
        state.missing_count = 0
        if not state.any_device_was_present:
            if trigger_barrier(config, state, "open", trigger_action):
                state.any_device_was_present = True
        else:
            logging.info("Разрешённое устройство всё ещё в зоне")
        return

    if state.any_device_was_present:
        state.missing_count += 1
        logging.info(
            "Разрешённое устройство не найдено (%s/%s)",
            state.missing_count,
            config.missing_threshold,
        )
        if state.missing_count >= config.missing_threshold:
            if trigger_barrier(config, state, "close", trigger_action):
                state.any_device_was_present = False
                state.missing_count = 0
    else:
        logging.info("Разрешённые устройства не найдены")
        state.missing_count = 0
