#!/usr/bin/env python3
"""
wujie_fand — 无界14+ (ARN39P) 现代风扇曲线守护进程
=================================================
特性:
  - 风扇停止待机 (低温下完全静音, 带启动防卡转脉冲)
  - 连续插值曲线 (非原厂9级跳变, 温度→PWM平滑映射)
  - 变化率限制 + 滞回, 杜绝转速振荡
  - 双风扇独立曲线 (CPU侧/GPU侧)
  - 失效安全: 温度失控→拉满; 传感器失效→恢复EC自动; 退出→恢复自动
  - 温度源: k10temp(sysfs, 首选) / EC XRAM 0x470 (备用)

用法:
  sudo python3 wujie_fand.py run [配置文件]     # 前台运行
  sudo python3 wujie_fand.py curve [配置文件]    # 打印曲线表
  sudo python3 wujie_fand.py restore            # 恢复EC自动控制

配置: /etc/wujie_fand.json (不存在则用内置默认)
"""
import sys
import os
import time
import json
import signal
import glob

# ============ EC访问 (I2EC via /dev/port) ============

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
    os.lseek(_fd, PORT_BASE, 0); os.write(_fd, bytes([0x2F]))
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


# ============ 寄存器 (逆向自EC V2.12) ============

FAN1 = dict(manual_pwm=0x021E, flag=0x021F, pwm_out=0x1809, tach_l=0x181E, tach_h=0x181F)
FAN2 = dict(manual_pwm=0x029E, flag=0x029F, pwm_out=0x1804, tach_l=0x1820, tach_h=0x1821)
EC_TEMP = 0x0470   # CPUT (0x9600越界, I2EC只能到0x7FFF)
FLAG_MANUAL = 0xDC
FLAG_AUTO = 0x00
PWM_HW_MAX = 184   # 硬件上限! >184风扇停转 (与EC固件斜坡钳位一致)


def fan_rpm(f):
    h = ec_read(f['tach_h']); l = ec_read(f['tach_l'])
    cnt = (h << 8) | l
    if cnt == 0 or cnt >= 0x4000:
        return 0
    if cnt < 0x80:
        return 9999
    return 2156250 // cnt


def restore_auto():
    """恢复EC自动风扇控制"""
    for f in (FAN1, FAN2):
        try:
            ec_write(f['flag'], FLAG_AUTO)
        except Exception:
            pass


# ============ 温度源 ============

def find_k10temp():
    """找k10temp sysfs路径"""
    for p in glob.glob('/sys/class/hwmon/hwmon*/name'):
        try:
            if open(p).read().strip() == 'k10temp':
                d = os.path.dirname(p)
                inputs = sorted(glob.glob(d + '/temp*_input'))
                return inputs
        except Exception:
            pass
    return []


def read_cpu_temp(k10_inputs):
    """返回CPU温度°C (k10temp优先, EC备用)"""
    temps = []
    for p in k10_inputs:
        try:
            t = int(open(p).read()) / 1000.0
            if 0 < t < 120:
                temps.append(t)
        except Exception:
            pass
    if temps:
        return max(temps)
    t = ec_read(EC_TEMP)
    if 0 < t < 120:
        return float(t)
    raise RuntimeError("温度源不可用")


# ============ 默认配置 ============

DEFAULT_CONFIG = {
    # 曲线: [[温度°C, PWM 0-255], ...] 线性插值
    # 按实测调优: 待机48-50°C(原厂29%转速可闻), 被动散热待机会稳定在~55°C
    "curve_fan1": [
        [50, 0],      # ≤50°C: 完全停转 (静音待机)
        [57, 50],     # 启动段 27% (软化: 原62太吵)
        [65, 78],     # 42% (软化: 原92)
        [73, 112],    # 61%
        [80, 150],    # 82%
        [86, 172],    # 93%
        [91, 184]     # 100% 硬件极限
    ],
    "curve_fan2": None,    # None = 用fan1曲线
    "fan2_offset": 0,     # 实测双风扇同型号(RPM差<1%), 不做偏移
    "min_pwm": 45,         # 运行中最低PWM (实测PWM40=1727rpm可靠, 低于此有失速风险)
    "hysteresis": 4.0,     # 停转/启动滞回°C: ≤50°C停, ≥54°C才重启
    "max_step": 15,        # 每秒最大PWM变化(防突变防振荡)
    "spinup_kick": 60,     # 从0启动时的脉冲PWM(防卡转)
    "spinup_hold": 2,      # 脉冲持续秒数
    "critical": 93,       # °C, 超过直接拉满并告警
    "interval": 1.0       # 轮询周期
}


def interp(temp, curve):
    """曲线线性插值"""
    if temp <= curve[0][0]:
        return curve[0][1]
    if temp >= curve[-1][0]:
        return curve[-1][1]
    for i in range(len(curve) - 1):
        t0, p0 = curve[i]
        t1, p1 = curve[i + 1]
        if t0 <= temp <= t1:
            if t1 == t0:
                return p1
            frac = (temp - t0) / (t1 - t0)
            return p0 + (p1 - p0) * frac
    return curve[-1][1]


class FanCtl:
    def __init__(self, cfg):
        self.cfg = cfg
        self.pwm = {1: None, 2: None}
        self.kick_until = {1: 0, 2: 0}
        self.fan_on = {1: False, 2: False}
        self.f2curve = cfg['curve_fan2'] or cfg['curve_fan1']
        # 曲线零点温度 (低于此值停转)
        self.zero_t = cfg['curve_fan1'][0][0]
        self.zero_t2 = self.f2curve[0][0]

    def target(self, fan, temp):
        base = interp(temp, self.cfg['curve_fan1'] if fan == 1 else self.f2curve)
        if fan == 2 and self.cfg['fan2_offset']:
            base = base + self.cfg['fan2_offset'] * PWM_HW_MAX / 100
        return int(max(0, min(PWM_HW_MAX, base)))

    def update(self, fan, temp, regs):
        zero = self.zero_t if fan == 1 else self.zero_t2
        hyst = self.cfg['hysteresis']

        if not self.fan_on[fan]:
            # 已停转: 只有温度 >= 零点+滞回 才重启 (防止温度在零点附近循环启停)
            if temp >= zero + hyst:
                self.fan_on[fan] = True
                # 启动脉冲防卡转
                self.kick_until[fan] = time.time() + self.cfg['spinup_hold']
                tgt = self.cfg['spinup_kick']
            else:
                tgt = 0
        else:
            # 运行中: 温度 < 零点 才停转
            if temp < zero:
                self.fan_on[fan] = False
                tgt = 0
            else:
                tgt = self.target(fan, temp)
                # 启动脉冲期间保持
                if time.time() < self.kick_until[fan]:
                    tgt = max(tgt, self.cfg['spinup_kick'])
                else:
                    # 最低运转PWM钳位: 防止曲线低段插值进入失速区(<40)
                    tgt = max(tgt, self.cfg['min_pwm'])

        # 变化率限制 (缓升缓降, 杜绝转速突变)
        cur = self.pwm[fan]
        if cur is not None and abs(tgt - cur) > self.cfg['max_step']:
            step = self.cfg['max_step'] if tgt > cur else -self.cfg['max_step']
            tgt = max(0, cur + step)

        tgt = max(0, min(PWM_HW_MAX, tgt))   # 硬件安全钳位
        self.pwm[fan] = tgt
        ec_write(regs['manual_pwm'], tgt)
        ec_write(regs['flag'], FLAG_MANUAL)

    def emergency(self, regs_list):
        for f in regs_list:
            ec_write(f['manual_pwm'], PWM_HW_MAX)   # 184! (255会停转!)
            ec_write(f['flag'], FLAG_MANUAL)


def load_config(path):
    cfg = dict(DEFAULT_CONFIG)
    if path and os.path.exists(path):
        with open(path) as fp:
            user = json.load(fp)
        cfg.update(user)
    return cfg


def main():
    if os.geteuid() != 0:
        print("请用 sudo 运行"); sys.exit(1)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    cfg_path = sys.argv[2] if len(sys.argv) > 2 else "/etc/wujie_fand.json"
    cfg = load_config(cfg_path)

    if cmd == "restore":
        restore_auto()
        print("[+] 已恢复EC自动风扇控制")
        return
    if cmd == "curve":
        print("Fan1 曲线 0-184刻度 (Fan2 = 同曲线{}%偏移):".format(
            f" {cfg['fan2_offset']}" if cfg['fan2_offset'] else "无"))
        for t, p in cfg['curve_fan1']:
            print(f"  {t:3d}°C → {p:3d}/184 ({p*100//184}%)")
        print(f"临界: {cfg['critical']}°C→255  停转阈值: 曲线0点  滞回: {cfg['hysteresis']}°C")
        return
    if cmd != "run":
        print(__doc__); sys.exit(1)

    print(f"[wujie_fand] 启动, 配置: {cfg_path}", flush=True)
    k10 = find_k10temp()
    print(f"[wujie_fand] 温度源: {'k10temp ' + str(len(k10)) + '路' if k10 else 'EC 0x9600'}")
    print(f"[wujie_fand] 手动模式运行中 (Ctrl+C / SIGTERM 恢复自动)")

    ctl = FanCtl(cfg)
    running = True

    def bye(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)

    err = 0
    try:
        while running:
            try:
                temp = read_cpu_temp(k10)
                err = 0
                if temp >= cfg['critical']:
                    print(f"[!] 临界温度 {temp}°C! 拉满双风扇")
                    ctl.emergency((FAN1, FAN2))
                else:
                    ctl.update(1, temp, FAN1)
                    ctl.update(2, temp, FAN2)
                r1, r2 = fan_rpm(FAN1), fan_rpm(FAN2)
                print(f"\r{temp:5.1f}°C  F1 {ctl.pwm[1]:3d}/184 {r1:5d}rpm  "
                      f"F2 {ctl.pwm[2]:3d}/184 {r2:5d}rpm   ", end='', flush=True)
            except Exception as e:
                err += 1
                print(f"\n[!] 错误: {e} ({err})")
                if err >= 5:
                    print("[!] 连续失败, 恢复EC自动并退出")
                    break
            time.sleep(cfg['interval'])
    finally:
        restore_auto()
        print("\n[wujie_fand] 已恢复EC自动风扇控制, 退出")


if __name__ == "__main__":
    main()
