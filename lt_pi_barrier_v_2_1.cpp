#include <algorithm>
#include <chrono>
#include <csignal>
#include <cstring>
#include <fcntl.h>
#include <iostream>
#include <regex>
#include <stdexcept>
#include <string>
#include <thread>
#include <unistd.h>
#include <vector>
#include <termios.h>
#include <sys/wait.h>

// =========================
// НАСТРОЙКИ
// =========================
static const std::string TARGET_MAC = "AA:BB:CC:DD:EE:FF";   // MAC телефона
static const std::string RELAY_PORT = "/dev/ttyUSB0";
static const int RELAY_BAUDRATE = 9600;

static const int SCAN_TIME = 8;          // сколько секунд сканировать Bluetooth
static const int CHECK_INTERVAL = 2;     // пауза между циклами
static const int COOLDOWN = 15;          // защита от повторного импульса на реле
static const int PULSE_TIME = 2;         // сколько держать реле включённым
static const int MISSING_THRESHOLD = 3;  // сколько циклов подряд телефон должен отсутствовать

// Команды для LCUS-1
static const std::vector<uint8_t> RELAY_ON_CMD  = {0xA0, 0x01, 0x01, 0xA2};
static const std::vector<uint8_t> RELAY_OFF_CMD = {0xA0, 0x01, 0x00, 0xA1};

static bool g_running = true;
static bool device_was_present = false;
static int missing_count = 0;
static std::chrono::steady_clock::time_point last_trigger_time =
    std::chrono::steady_clock::now() - std::chrono::seconds(COOLDOWN);


void signal_handler(int)
{
    g_running = false;
}


void log(const std::string& msg)
{
    std::cout << msg << std::endl;
}


bool validate_mac(const std::string& mac)
{
    static const std::regex mac_regex(R"(^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$)");
    return std::regex_match(mac, mac_regex);
}


std::string to_upper(std::string s)
{
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
    return s;
}


struct CmdResult
{
    bool ok;
    std::string output;
};


CmdResult run_cmd(const std::string& cmd)
{
    FILE* pipe = popen(cmd.c_str(), "r");
    if (!pipe) {
        return {false, ""};
    }

    std::string output;
    char buffer[512];

    while (fgets(buffer, sizeof(buffer), pipe) != nullptr) {
        output += buffer;
    }

    int rc = pclose(pipe);
    bool ok = false;

    if (WIFEXITED(rc)) {
        ok = (WEXITSTATUS(rc) == 0);
    }

    return {ok, output};
}


void bluetooth_power_on()
{
    const std::string cmd =
        "printf 'power on\nagent on\ndefault-agent\nquit\n' | bluetoothctl 2>&1";

    CmdResult result = run_cmd(cmd);

    if (result.ok) {
        log("Bluetooth инициализирован");
    } else {
        log("Не удалось нормально инициализировать Bluetooth: " + result.output);
    }
}


bool scan_once(int scan_time, std::string& devices_output)
{
    // Запускаем сканирование на несколько секунд.
    // timeout почти всегда завершает команду сам, так что код возврата тут не очень показателен.
    std::string scan_cmd =
        "timeout " + std::to_string(scan_time) + "s bluetoothctl scan on >/dev/null 2>&1";
    std::system(scan_cmd.c_str());

    // Потом отдельно читаем список устройств.
    CmdResult devices = run_cmd("bluetoothctl devices 2>&1");
    if (!devices.ok) {
        log("Не удалось получить список Bluetooth-устройств");
        devices_output.clear();
        return false;
    }

    devices_output = devices.output;
    return true;
}


bool is_target_present(const std::string& devices_output, const std::string& target_mac)
{
    return to_upper(devices_output).find(to_upper(target_mac)) != std::string::npos;
}


speed_t baudrate_to_constant(int baudrate)
{
    switch (baudrate) {
        case 9600: return B9600;
        case 19200: return B19200;
        case 38400: return B38400;
        case 57600: return B57600;
        case 115200: return B115200;
        default:
            throw std::runtime_error("Неподдерживаемый baudrate");
    }
}


class SerialPort
{
public:
    SerialPort(const std::string& port, int baudrate)
    {
        fd_ = open(port.c_str(), O_RDWR | O_NOCTTY | O_SYNC);
        if (fd_ < 0) {
            throw std::runtime_error("Не удалось открыть порт " + port + ": " + std::strerror(errno));
        }

        termios tty{};
        if (tcgetattr(fd_, &tty) != 0) {
            close(fd_);
            throw std::runtime_error("tcgetattr() failed: " + std::string(std::strerror(errno)));
        }

        cfsetospeed(&tty, baudrate_to_constant(baudrate));
        cfsetispeed(&tty, baudrate_to_constant(baudrate));

        tty.c_cflag = (tty.c_cflag & ~CSIZE) | CS8;
        tty.c_iflag &= ~IGNBRK;
        tty.c_lflag = 0;
        tty.c_oflag = 0;
        tty.c_cc[VMIN] = 0;
        tty.c_cc[VTIME] = 10;

        tty.c_iflag &= ~(IXON | IXOFF | IXANY);
        tty.c_cflag |= (CLOCAL | CREAD);
        tty.c_cflag &= ~(PARENB | PARODD);
        tty.c_cflag &= ~CSTOPB;
        tty.c_cflag &= ~CRTSCTS;

        if (tcsetattr(fd_, TCSANOW, &tty) != 0) {
            close(fd_);
            throw std::runtime_error("tcsetattr() failed: " + std::string(std::strerror(errno)));
        }
    }

    ~SerialPort()
    {
        if (fd_ >= 0) {
            close(fd_);
        }
    }

    void write_bytes(const std::vector<uint8_t>& data)
    {
        ssize_t written = write(fd_, data.data(), data.size());
        if (written < 0 || static_cast<size_t>(written) != data.size()) {
            throw std::runtime_error("Ошибка записи в serial: " + std::string(std::strerror(errno)));
        }

        if (tcdrain(fd_) != 0) {
            throw std::runtime_error("Ошибка tcdrain(): " + std::string(std::strerror(errno)));
        }
    }

private:
    int fd_ = -1;
};


void pulse_relay(SerialPort& ser)
{
    ser.write_bytes(RELAY_ON_CMD);

    try {
        std::this_thread::sleep_for(std::chrono::seconds(PULSE_TIME));
    } catch (...) {
        try {
            ser.write_bytes(RELAY_OFF_CMD);
        } catch (...) {
        }
        throw;
    }

    ser.write_bytes(RELAY_OFF_CMD);
}


bool trigger_barrier(SerialPort& ser, const std::string& action)
{
    auto now = std::chrono::steady_clock::now();
    auto diff = std::chrono::duration_cast<std::chrono::seconds>(now - last_trigger_time).count();

    if (diff < COOLDOWN) {
        log("Импульс '" + action + "' заблокирован cooldown-ом");
        return false;
    }

    if (action == "open") {
        log(">>> Телефон найден, открываем шлагбаум");
    } else if (action == "close") {
        log("<<< Телефон удалился, закрываем шлагбаум");
    } else {
        log("*** Выполняем действие: " + action);
    }

    try {
        pulse_relay(ser);
    } catch (const std::exception& e) {
        log(std::string("Ошибка работы с реле: ") + e.what());
        return false;
    }

    last_trigger_time = now;
    return true;
}


int main()
{
    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    if (TARGET_MAC == "AA:BB:CC:DD:EE:FF") {
        log("Ошибка: укажи TARGET_MAC в файле");
        return 1;
    }

    if (!validate_mac(TARGET_MAC)) {
        log("Ошибка: некорректный TARGET_MAC: " + TARGET_MAC);
        return 1;
    }

    log("Инициализация Bluetooth...");
    bluetooth_power_on();

    log("Открытие порта реле: " + RELAY_PORT);

    try {
        SerialPort ser(RELAY_PORT, RELAY_BAUDRATE);

        while (g_running) {
            log("Сканирование BLE...");

            std::string devices_output;
            bool scan_ok = scan_once(SCAN_TIME, devices_output);

            if (!devices_output.empty()) {
                log("Найденные устройства:");
                log(devices_output);
            } else {
                log("Список устройств пуст");
            }

            if (!scan_ok) {
                log("Ошибка сканирования, состояние не меняем");
                std::this_thread::sleep_for(std::chrono::seconds(CHECK_INTERVAL));
                continue;
            }

            bool device_is_present = is_target_present(devices_output, TARGET_MAC);

            if (device_is_present) {
                missing_count = 0;

                if (!device_was_present) {
                    bool triggered = trigger_barrier(ser, "open");
                    if (triggered) {
                        device_was_present = true;
                    }
                } else {
                    log("Телефон всё ещё в зоне действия");
                }

            } else {
                if (device_was_present) {
                    missing_count++;
                    log(
                        "Телефон не найден (" +
                        std::to_string(missing_count) + "/" +
                        std::to_string(MISSING_THRESHOLD) +
                        "), ждём подтверждение удаления"
                    );

                    if (missing_count >= MISSING_THRESHOLD) {
                        bool triggered = trigger_barrier(ser, "close");
                        if (triggered) {
                            device_was_present = false;
                            missing_count = 0;
                        }
                    }
                } else {
                    log("Целевой телефон не найден");
                    missing_count = 0;
                }
            }

            std::this_thread::sleep_for(std::chrono::seconds(CHECK_INTERVAL));
        }

    } catch (const std::exception& e) {
        log(std::string("Критическая ошибка: ") + e.what());
        return 1;
    }

    log("Остановлено");
    return 0;
}