#!/usr/bin/env python3
"""
wujie_kblight — 无界14+ 键盘背光常亮守护
========================================
原理(逆向EC V2.12):
  XRAM 0x03E2 = 背光等级 (0=关, 0x5C=半亮, 0xB8=全亮, F9循环)
  XRAM 0x03E1 = 超时倒数(秒), 减到0关背光; 0xFF=禁用倒数
  但任何键盘按键都会把它刷回30秒(KBTT=0默认) → 必须持续压制

行为:
  每秒检查: 背光开着(等级≠0) → 强制倒数=0xFF(常亮)
  背光关着(F9循环到0) → 不干预
  F9切换亮度照常工作, 只是不再自动熄灭
  进程退出 → 回到原厂"30秒无操作熄灭"行为(失效安全)

用法:
  sudo python3 wujie_kblight.py run      # 前台运行(Ctrl+C退出)
  sudo python3 wujie_kblight.py status    # 查看当前状态
"""
import sys
import os
import time

PORT_BASE = 0x4E
_fd = None


def _pwrite(reg, val):
    global _fd
    if _fd is None:
        _fd = os.open("/dev/port", os.O_RDWR | os.O_SYNC)
    os.lseek(_fd, PORT_BASE, 0); os.write(_fd, bytes([0x2E]))
    os.lseek(_fd, PORT_BASE + 1, 0); os.write(_fd, bytes([reg]))
    os.lseek(_fd, PORT_BASE, 0); os.write(_fd, bytes([0x2F]))
    os.lseek(_fd, PORT_BASE + 1, 0); os.write(_fd, bytes([val & 0xFF]))


def _pread(reg):
    global _fd
    if _fd is None:
        _fd = os.open("/dev/port", os.O_RDWR | os.O_SYNC)
    os.lseek(_fd, PORT_BASE, 0); os.write(_fd, bytes([0x2E]))
    os.lseek(_fd, PORT_BASE + 1, 0); os.write(_fd, bytes([reg]))
    os.lseek(fd := _fd, PORT_BASE, 0); os.write(_fd, bytes([0x2F]))
    os.lseek(_fd, PORT_BASE + 1, 0)
    return os.read(_fd, 1)[0]


def ec_read(addr):
    _pwrite(0x10, addr & 0xFF)
    _pwrite(0x11, addr >> 8)
    return _pread(0x12)


def ec_write(addr, val):
    _pwrite(0x10, addr & 0xFF)
    _pwrite(0x11, addr >> 8)
    _pwrite(0x12, val)


LEVEL = 0x03E2      # 背光等级: 0 / 0x5C(92) / 0xB8(184)
COUNTDOWN = 0x03E1  # 超时倒数: 秒; 0xFF=禁用
LVL_NAMES = {0: "关", 0x5C: "半亮(92)", 0xB8: "全亮(184)"}


def status():
    lvl = ec_read(LEVEL)
    cnt = ec_read(COUNTDOWN)
    print(f"背光等级: 0x{lvl:02X} ({LVL_NAMES.get(lvl, '?')})")
    print(f"超时倒数: 0x{cnt:02X} "
          f"({'常亮(0xFF)' if cnt == 0xFF else str(cnt) + '秒'})")


def run():
    if os.geteuid() != 0:
        print("请用 sudo 运行"); sys.exit(1)
    print("[kblight] 背光常亮守护启动 (Ctrl+C退出恢复30秒超时)")
    last_lvl = -1
    try:
        while True:
            lvl = ec_read(LEVEL)
            if lvl != 0:
                # 背光开着 → 保持倒数=0xFF(按键刷回的30秒在1秒内被覆盖)
                ec_write(COUNTDOWN, 0xFF)
            if lvl != last_lvl:
                print(f"[kblight] 背光: {LVL_NAMES.get(lvl, hex(lvl))}"
                      f"{' — 常亮压制中' if lvl else ' (关闭,不干预)'}")
                last_lvl = lvl
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        # 恢复30秒超时(原厂行为)
        if ec_read(LEVEL) != 0:
            ec_write(COUNTDOWN, 30)
        print("\n[kblight] 退出, 已恢复原厂30秒超时行为")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "status":
        if os.geteuid() != 0:
            print("请用 sudo 运行"); sys.exit(1)
        status()
    else:
        print(__doc__)
