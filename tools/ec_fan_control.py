#!/usr/bin/env python3
"""
无界14+ (ARN39P) ITE EC 风扇控制工具 - Linux版
通过 I2EC 协议读写 EC XRAM

用法 (需要root):
  sudo python3 ec_fan_control.py status          # 读取温度/转速/等级/PWM
  sudo python3 ec_fan_control.py monitor          # 持续监控 (Ctrl+C退出)
  sudo python3 ec_fan_control.py set 200          # 双风扇手动PWM (0-255)
  sudo python3 ec_fan_control.py setfan1 200     # 仅Fan1
  sudo python3 ec_fan_control.py setfan2 180      # 仅Fan2
  sudo python3 ec_fan_control.py auto            # 恢复EC自动控制
  sudo python3 ec_fan_control.py curve 60 70      # 演示: 写自定义曲线等级

寄存器映射 (逆向自EC固件 V2.12):
  0x0470      CPU温度°C (I2EC可达; 0x9600越界不可读)
  0x0213/0x0293  风扇等级 (0-8)
  0x0214/0x0294  PWM目标
  0x021E/0x029E  手动PWM值 (0xDC模式)
  0x021F/0x029F  模式标志: 0=自动, 0xA5=自定义曲线, 0xDC=手动PWM
  0x1809/0x1804  PWM硬件输出 (0-255)
  0x181E/F, 0x1820/21  转速计数 (RPM=2156250/count)
"""
import sys
import time
import os

try:
    import portio  # 需要 python-portio 或改用 /dev/port
except ImportError:
    pass

# ============ 底层端口IO (通过 /dev/port, 无需额外驱动) ============

_port_fd = None


def _open_port():
    global _port_fd
    if _port_fd is None:
        _port_fd = os.open("/dev/port", os.O_RDWR | os.O_SYNC)
    return _port_fd


def outb(value, port):
    fd = _open_port()
    os.lseek(fd, port, os.SEEK_SET)
    os.write(fd, bytes([value & 0xFF]))


def inb(port):
    fd = _open_port()
    os.lseek(fd, port, os.SEEK_SET)
    return os.read(fd, 1)[0]


# ============ I2EC 协议 ============

# PNPCFG端口基址: 无界16Pro/14X实测为0x4E, 部分机型为0x2E
# 本工具自动探测
PORT_BASES = [0x4E, 0x2E]
PORT_BASE = None


def _i2ec_select(base, reg):
    """选择I2EC寄存器"""
    outb(0x2E, base)      # 地址相位标记
    outb(reg, base + 1)   # 寄存器号


def _i2ec_commit(base, val):
    """写入数据到已选择的寄存器"""
    outb(0x2F, base)      # 数据相位标记
    outb(val, base + 1)


def _i2ec_read_reg(base, reg):
    _i2ec_select(base, reg)
    outb(0x2F, base)
    return inb(base + 1)


def detect_port_base():
    """探测PNPCFG端口基址: 读温度(0x470=CPUT, I2EC可达范围)应在0-100°C"""
    global PORT_BASE
    for base in PORT_BASES:
        try:
            ec_write_addr(base, 0x0470)
            t = ec_read_data(base)
            if 0 < t <= 100:
                PORT_BASE = base
                return base
        except Exception:
            pass
    raise RuntimeError("无法探测I2EC端口基址 (试过 0x4E/0x2E)")


def ec_write_addr(base, addr):
    """设置I2EC 16位地址"""
    _i2ec_select(base, 0x10); _i2ec_commit(base, addr & 0xFF)
    _i2ec_select(base, 0x11); _i2ec_commit(base, addr >> 8)


def ec_read_data(base):
    _i2ec_select(base, 0x12)
    outb(0x2F, base)
    return inb(base + 1)


def ec_write(addr, val):
    """写EC XRAM 16位地址"""
    base = PORT_BASE or detect_port_base()
    _i2ec_select(base, 0x10); _i2ec_commit(base, addr & 0xFF)   # ADDR_L
    _i2ec_select(base, 0x11); _i2ec_commit(base, addr >> 8)    # ADDR_H
    _i2ec_select(base, 0x12); _i2ec_commit(base, val)          # DATA


def ec_read(addr):
    """读EC XRAM 16位地址"""
    base = PORT_BASE or detect_port_base()
    _i2ec_select(base, 0x10); _i2ec_commit(base, addr & 0xFF)
    _i2ec_select(base, 0x11); _i2ec_commit(base, addr >> 8)
    _i2ec_select(base, 0x12)
    outb(0x2F, base)
    return inb(base + 1)


# ============ 高层接口 ============

# 注: I2EC只能访问 XRAM 0x0000-0x7FFF; 0x9600(风扇原始温度源)越界, 用0x470(CPUT)替代
EC_TEMP_ADDR = 0x0470

# Fan1 (CPU侧) / Fan2 (GPU侧)
FAN_REGS = {
    1: dict(level=0x0213, pwm_target=0x0214, pwm_cur=0x0215,
            manual_pwm=0x021E, flag=0x021F, pwm_out=0x1809,
            tach_l=0x181E, tach_h=0x181F),
    2: dict(level=0x0293, pwm_target=0x0294, pwm_cur=0x0295,
            manual_pwm=0x029E, flag=0x029F, pwm_out=0x1804,
            tach_l=0x1820, tach_h=0x1821),
}

FLAG_NAMES = {0x00: "自动", 0xA5: "自定义曲线", 0xCC: "自定义温度", 0xDC: "手动PWM"}


def cpu_temp():
    return ec_read(EC_TEMP_ADDR)


def fan_rpm(fan):
    r = FAN_REGS[fan]
    h = ec_read(r['tach_h'])
    l = ec_read(r['tach_l'])
    cnt = (h << 8) | l
    if cnt == 0 or cnt >= 0x4000:
        return 0
    if cnt < 0x80:
        return 9999
    return 2156250 // cnt


def fan_info(fan):
    r = FAN_REGS[fan]
    return dict(
        flag=ec_read(r['flag']),
        level=ec_read(r['level']),
        pwm_target=ec_read(r['pwm_target']),
        pwm_cur=ec_read(r['pwm_cur']),
        pwm_out=ec_read(r['pwm_out']),
        rpm=fan_rpm(fan),
    )


PWM_HW_MAX = 184  # 硬件上限! >184风扇直接停转(EC斜坡逻辑同样钳位于此)


def set_manual(fan, pwm):
    """手动PWM模式 (0-184, 超过184硬件会停转!)"""
    r = FAN_REGS[fan]
    pwm = int(pwm)
    if pwm > PWM_HW_MAX:
        print(f"[!] PWM {pwm} 超过硬件上限184 — 已钳位 (超过184风扇会直接停转!)")
        pwm = PWM_HW_MAX
    pwm = max(0, pwm)
    ec_write(r['manual_pwm'], pwm)
    ec_write(r['flag'], 0xDC)
    print(f"Fan{fan}: 手动PWM = {pwm}/184 ({pwm*100//184}%)")


def set_auto(fan):
    r = FAN_REGS[fan]
    ec_write(r['flag'], 0x00)
    print(f"Fan{fan}: 恢复EC自动控制")


def status():
    t = cpu_temp()
    print(f"CPU温度: {t}°C (XRAM 0x470 CPUT)")
    for fan in (1, 2):
        i = fan_info(fan)
        print(f"Fan{fan}: 模式={FLAG_NAMES.get(i['flag'], hex(i['flag']))} "
              f"等级={i['level']}/8 PWM目标={i['pwm_target']}/255 "
              f"输出={i['pwm_out']}/255 ({i['pwm_out']*100//255}%) "
              f"转速={i['rpm']}RPM")


def monitor():
    print("持续监控中 (Ctrl+C 退出)...")
    try:
        while True:
            t = cpu_temp()
            f1, f2 = fan_info(1), fan_info(2)
            print(f"\rCPU {t:3d}°C | F1 L{f1['level']} {f1['pwm_out']:3d}/255 "
                  f"{f1['rpm']:5d}RPM | F2 L{f2['level']} {f2['pwm_out']:3d}/255 "
                  f"{f2['rpm']:5d}RPM  ", end='', flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print()


def sweep():
    """PWM→RPM响应扫描: 测出真实曲线, 找饱和点"""
    print("PWM-RPM响应扫描 (每档稳定4秒, Ctrl+C中止)")
    print(f"{'PWM':>4} {'占空':>5} {'Fan1':>8} {'Fan2':>8}")
    points = [40, 60, 80, 100, 120, 140, 160, 173, 178, 182, 184, 186, 190]
    results = []
    try:
        for pwm in points:
            set_manual(1, pwm)
            set_manual(2, pwm)
            time.sleep(4)
            r1, r2 = fan_rpm(1), fan_rpm(2)
            results.append((pwm, r1, r2))
            print(f"{pwm:>4} {pwm*100//255:>4}% {r1:>7}rpm {r2:>7}rpm", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        set_auto(1)
        set_auto(2)
        print("\n已恢复EC自动控制")
        # 简单分析 (只统计有效运转点, 排除>184的停转死区)
        print("\n分析:")
        alive = [(p, r1, r2) for p, r1, r2 in results if r1 > 0]
        dead = [(p, r1, r2) for p, r1, r2 in results if r1 == 0 and p > 100]
        if alive:
            top = alive[-1]
            print(f"  有效最大: PWM {top[0]} → {top[1]}rpm (Fan1)")
            print(f"  响应斜率: {(top[1]-alive[0][1])/(top[0]-alive[0][0]):.1f} RPM/PWM单位 (线性度良好)")
        if dead:
            print(f"  停转死区: PWM ≥ {dead[0][0]} 风扇直接停转 (硬件边界!)")
            print(f"  → 曲线/手动值必须 ≤ {dead[0][0]-1}")


def pwmregs():
    """PWM外设寄存器三态对比: 自动/手动184(满)/手动200(停转)
    目的: 找到PWM周期寄存器, 验证184=100%占空比"""
    import subprocess

    def dump(label):
        regs = {}
        for a in range(0x1800, 0x1822):
            regs[a] = ec_read(a)
        print(f"\n--- {label} ---")
        for i, a in enumerate(range(0x1800, 0x1822)):
            v = regs[a]
            if i % 8 == 0:
                print(f"  0x{a:04X}:", end="")
            mark = ""
            if a in (0x1804, 0x1809):
                mark = " ←F2/F1 duty"
            print(f" {v:02X}", end="")
            if i % 8 == 7 or a == 0x1821:
                print(mark)
        return regs

    # 状态1: 当前(自动模式下的值)
    r_auto = dump("状态1: EC自动模式(当前)")

    # 状态2: 手动184
    set_manual(1, 184)
    set_manual(2, 184)
    time.sleep(3)
    r_184 = dump("状态2: 手动PWM=184 (风扇5720rpm全速)")

    # 状态3: 手动200 (已知停转)
    set_manual(1, 200)
    set_manual(2, 200)
    time.sleep(3)
    r_200 = dump("状态3: 手动PWM=200 (风扇停转!)")

    # 恢复
    set_auto(1)
    set_auto(2)
    print("\n已恢复EC自动控制")

    # 差异分析
    print("\n=== 三态差异 (0x1800-0x1821) ===")
    print(f"{'地址':>8} {'自动':>6} {'184':>6} {'200':>6}  解读")
    for a in range(0x1800, 0x1822):
        v1, v2, v3 = r_auto[a], r_184[a], r_200[a]
        if len({v1, v2, v3}) > 1:
            note = ""
            if a in (0x1804, 0x1809):
                note = "风扇duty寄存器(预期变化)"
            elif v1 == v2 == v3:
                note = ""
            else:
                note = "★ 配置/状态寄存器变化!"
            print(f"0x{a:04X}   {v1:02X}    {v2:02X}    {v3:02X}   {note}")
    print("\n判读: 若某寄存器恒为0xB8(184) → 它是PWM周期, duty184/184=100%全速")
    print("      若0x1804/0x1809随写入变化而其他不变 → >184的停转是硬件比较器行为")


def main():
    if os.geteuid() != 0:
        print("请用 sudo 运行 (需要 /dev/port 访问权限)")
        sys.exit(1)

    base = detect_port_base()
    print(f"[I2EC] 使用端口基址 0x{base:02X}/0x{base+1:02X}")

    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        status()
    elif cmd == "monitor":
        monitor()
    elif cmd == "set":
        v = int(sys.argv[2])
        set_manual(1, v)
        set_manual(2, v)
    elif cmd == "setfan1":
        set_manual(1, int(sys.argv[2]))
    elif cmd == "setfan2":
        set_manual(2, int(sys.argv[2]))
    elif cmd == "auto":
        set_auto(1)
        set_auto(2)
    elif cmd == "sweep":
        sweep()
    elif cmd == "pwmregs":
        pwmregs()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
