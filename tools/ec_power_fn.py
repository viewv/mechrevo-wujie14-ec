#!/usr/bin/env python3
"""
无界14+ (ARN39P) EC实时状态读取/设置工具 - 电源+Fn键部分
通过 MMIO 窗口 0xFE0B0400 (EC XRAM 0x400-0x4FF) 访问

用法 (root):
  sudo python3 ec_power_fn.py status      # 读取电源/Fn/电池相关全部寄存器
  sudo python3 ec_power_fn.py fnlock on   # 开启Fn锁 (F1-F12直接媒体功能, EC自动持久化!)
  sudo python3 ec_power_fn.py fnlock off  # 关闭Fn锁
  sudo python3 ec_power_fn.py mode        # 读当前性能模式
  sudo python3 ec_power_fn.py setmode N   # 设置性能模式 0-3 (写FCMI=0x80+N)
  sudo python3 ec_power_fn.py battery     # 电池信息
  sudo python3 ec_power_fn.py charge stop # 实验性: 停止充电 (I2EC写XRAM 0x0581 bit5)
  sudo python3 ec_power_fn.py charge start# 实验性: 恢复充电
  sudo python3 ec_power_fn.py i2ec rd 0x0581          # 读任意EC XRAM
  sudo python3 ec_power_fn.py i2ec wr 0x0581 0x20     # 写任意EC XRAM

寄存器 (逆向EC V2.12 + DSDT确认, 均为EC XRAM地址):
  0x404 OSFG  OS标志 (Fn锁生效需要它≠0)
  0x41A FNLK  Fn锁: 0=需按Fn, 1=F1-F12直接媒体键 (EC自动持久化到flash!)
  0x431 FCMO  当前性能模式 (0-3)
  0x432 FCMI  模式切换命令 (写0x80+N)
  0x470 CPUT  CPU温度
  0x480.. 电池状态区
  0x494/0x495 BCTH/BCTL 充电阈值 (固件V2.12未实现, 写了没用)
  0x4B0/B1 VER1/2 EC版本
  0x581 bit5 充电使能 (置1=停充, 清0=恢复; 需I2EC访问, 实验性)
"""
import sys
import os
import time
import mmap

WINDOW_BASE = 0xFE0B0400   # EC XRAM 0x400 的MMIO映射
PAGE = mmap.PAGESIZE

# 字段: EC XRAM绝对地址
F = {
    'OSFG': 0x404, 'FMFG': 0x405, 'RBFG': 0x412, 'TPTL': 0x414,
    'FFLG': 0x417, 'FFFG': 0x418, 'FNLK': 0x41A, 'WCAM': 0x413,
    'FCMO': 0x431, 'FCMI': 0x432, 'KBTT': 0x434, 'BHKF': 0x435,
    'SPMF': 0x440, 'LIDF': 0x451, 'CPUT': 0x470,
    'ACST': 0x480, 'BATM': 0x481, 'BAST': 0x48C,
    'BRSC': 0x493, 'BCTH': 0x494, 'BCTL': 0x495,
    'VER1': 0x4B0, 'VER2': 0x4B1,
}


class ECWindow:
    """EC MMIO窗口 (0xFE0B0400 = XRAM 0x400-0x4FF)"""

    def __init__(self):
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        map_off = WINDOW_BASE & ~(PAGE - 1)          # 页对齐基址 0xFE0B0000
        self.sub = WINDOW_BASE - map_off             # 窗口在页内偏移 0x400
        # 映射足够覆盖窗口
        length = (self.sub + 0x100 + PAGE - 1) & ~(PAGE - 1)
        self.mm = mmap.mmap(fd, length, mmap.MAP_SHARED,
                            mmap.PROT_READ | mmap.PROT_WRITE, offset=map_off)
        os.close(fd)

    def rd(self, xram_addr, n=1):
        """读EC XRAM (绝对地址, 需在0x400-0x4FF窗口内)"""
        off = self.sub + (xram_addr - 0x400)
        return list(self.mm[off:off + n])

    def wr(self, xram_addr, val):
        off = self.sub + (xram_addr - 0x400)
        self.mm[off] = val & 0xFF

    def u16(self, xram_addr):
        b = self.rd(xram_addr, 2)
        return b[0] | (b[1] << 8)


# ============ I2EC协议 (访问任意EC XRAM, 如0x581) ============

I2EC_PORTS = [0x4E, 0x2E]   # 依无界16Pro/14X经验先试0x4E


class I2EC:
    def __init__(self):
        self.base = None

    def _raw(self, base, reg, val=None):
        """I2EC寄存器访问: 索引口写0x2E选寄存器, 0x2F传数据"""
        fd = os.open("/dev/port", os.O_RDWR | os.O_SYNC)
        try:
            os.lseek(fd, base, 0); os.write(fd, bytes([0x2E]))
            os.lseek(fd, base + 1, 0); os.write(fd, bytes([reg]))
            os.lseek(fd, base, 0); os.write(fd, bytes([0x2F]))
            os.lseek(fd, base + 1, 0)
            if val is None:
                return os.read(fd, 1)[0]
            os.write(fd, bytes([val & 0xFF]))
            return None
        finally:
            os.close(fd)

    def detect(self):
        """探测端口基址: 读XRAM 0x470(CPUT温度)应在0-100°C"""
        if self.base:
            return self.base
        for base in I2EC_PORTS:
            try:
                self._raw(base, 0x10, 0x70)   # ADDR_L
                self._raw(base, 0x11, 0x04)   # ADDR_H → 0x0470
                t = self._raw(base, 0x12)     # DATA
                if 0 < t <= 100:
                    self.base = base
                    print(f"[I2EC] 端口基址 0x{base:02X}/0x{base+1:02X} 探测成功 (CPUT={t}°C)")
                    return base
            except OSError:
                continue
        raise RuntimeError("I2EC端口探测失败 (试过0x4E/0x2E)")

    def read(self, addr):
        b = self.detect()
        self._raw(b, 0x10, addr & 0xFF)
        self._raw(b, 0x11, addr >> 8)
        return self._raw(b, 0x12)

    def write(self, addr, val):
        b = self.detect()
        self._raw(b, 0x10, addr & 0xFF)
        self._raw(b, 0x11, addr >> 8)
        self._raw(b, 0x12, val)


i2ec = I2EC()
CHARGE_CTRL = 0x0581  # bit5 = 停充 (逆向自EC状态机0x116B1/0x11686, 实验性!)


# ============ 命令实现 ============

def cmd_status(ec):
    print("=" * 58)
    print(" EC 实时状态  (MMIO 0xFE0B0400 = EC XRAM 0x400-0x4FF)")
    print("=" * 58)

    print(f" EC版本字段   : {ec.rd(F['VER1'])[0]:02X}.{ec.rd(F['VER2'])[0]:02X}")

    fnlk = ec.rd(F['FNLK'])[0]
    osfg = ec.rd(F['OSFG'])[0]
    print(f" FNLK (Fn锁)  : {fnlk}   ({'已锁定: F1-F12=直接媒体键' if fnlk else '未锁定: 需按Fn+Fx'})")
    print(f" OSFG (OS标志): {osfg}   ({'OS就绪' if osfg else '未就绪! Fn锁需此位≠0'})")
    print(f" → Fn锁实际状态: {'★ 生效中' if (fnlk and osfg) else '未生效'}")

    fcmo = ec.rd(F['FCMO'])[0]
    modes = {0: '模式0', 1: '模式1(平衡?)', 2: '模式2(性能?)', 3: '模式3(狂飙?)'}
    print(f" FCMO (性能模式): {fcmo} ({modes.get(fcmo, '?')})")
    print(f" CPUT (CPU温度): {ec.rd(F['CPUT'])[0]}°C")

    acst = ec.rd(F['ACST'])[0] & 0x01
    print(f" ACST (适配器): {'已插入' if acst else '电池供电'}")
    bast = ec.rd(F['BAST'])[0] & 0x0F
    bst = {0: '放电', 1: '充电中', 2: '停止充电', 3: '?'}.get(bast, '?')
    print(f" BAST (电池状态): {bast} ({bst})")
    print(f" BRSC (电池电量): {ec.rd(F['BRSC'])[0]}%")
    print(f" BCTH/BCTL (充电阈值): {ec.rd(F['BCTH'])[0]}/{ec.rd(F['BCTL'])[0]} (V2.12固件未实现,仅供参考)")
    print(f" LIDF (复合标志): 0x{ec.rd(F['LIDF'])[0]:02X} (bit0=80%停充态, bit1=80%模式, bit2=满电监控)")
    print(f" TPTL: 0x{ec.rd(F['TPTL'])[0]:02X}  KBTT: {ec.rd(F['KBTT'])[0]}  SPMF: 0x{ec.rd(F['SPMF'])[0]:02X}")

    if acst:
        try:
            cur = i2ec.read(CHARGE_CTRL)
            print(f" I2EC充电控制位 (XRAM 0x581): 0x{cur:02X} (bit5={'1=已停充' if cur & 0x20 else '0=充电允许'})")
        except Exception as e:
            print(f" I2EC: {e}")


def cmd_fnlock(ec, on):
    if on:
        if ec.rd(F['OSFG'])[0] == 0:
            print("[i] OSFG=0, 一并置1 (EC固件本应在OS就绪时设置)")
            ec.wr(F['OSFG'], 1)
        ec.wr(F['FNLK'], 1)
        print("[+] Fn锁已开启: F1-F12将直接输出媒体功能键")
        print("[i] EC检测到变化后会自动持久化到flash (重启仍生效)")
        print("[i] 按住Fn+Fx则临时恢复标准F1-F12功能")
    else:
        ec.wr(F['FNLK'], 0)
        print("[+] Fn锁已关闭 (恢复需按Fn+Fx才出媒体键)")
    time.sleep(0.2)
    cmd_status(ec)


def cmd_battery(ec):
    bpr = ec.u16(0x48D)
    brc = ec.u16(0x48F)
    bpv = ec.u16(0x491)
    print(f" 充放电率 BPR: {bpr} mW")
    print(f" 剩余容量 BRC: {brc} mWh")
    print(f" 电压 BPV: {bpv} mV")
    print(f" 满充容量 BFC: {ec.u16(0x488)} mWh")
    print(f" 电池状态 BAST: {ec.rd(F['BAST'])[0] & 0xF}")


def cmd_charge(ec, arg):
    """实验性充电控制 (XRAM 0x581 bit5)"""
    cur = i2ec.read(CHARGE_CTRL)
    print(f"[i] XRAM 0x{CHARGE_CTRL:04X} 当前值: 0x{cur:02X} (bit5={'1停充' if cur & 0x20 else '0充电'})")
    if arg == "stop":
        i2ec.write(CHARGE_CTRL, cur | 0x20)
        print("[+] 已写入停充指令 (bit5=1)")
    elif arg == "start":
        i2ec.write(CHARGE_CTRL, cur & ~0x20 & 0xFF)
        print("[+] 已写入恢复充电指令 (bit5=0)")
    else:
        print("用法: charge stop|start")
        return
    time.sleep(1.5)
    new = i2ec.read(CHARGE_CTRL)
    print(f"[i] 1.5秒后回读: 0x{new:02X} (bit5={'1停充' if new & 0x20 else '0充电'})")
    if (new & 0x20) != (cur & 0x20):
        print("[✓] EC接受了写入!")
    else:
        print("[!] bit未变化 — EC可能回写了此位(与内部状态机冲突)")
    bast = ec.rd(F['BAST'])[0] & 0xF
    print(f"[i] BAST={bast} (1=充电,2=停止)  电量={ec.rd(F['BRSC'])[0]}%  功率={ec.u16(0x48D)}mW")
    print("[!] 请观察: 充电指示灯是否熄灭 / 系统设置里充电状态是否变为'放电'")
    print("[!] 若确认有效, 记得测完恢复: charge start")


def cmd_arm80(ec, on):
    """武装/解除EC内置的80%充电维护模式 (LIDF bit1)"""
    cur = ec.rd(F['LIDF'])[0]
    if on:
        ec.wr(F['LIDF'], cur | 0x02)
        print("[+] 已置 LIDF bit1 (武装EC内置80%维护模式)")
        print("[i] EC状态机应自动执行: 电池≥81% → 停充, ≤80% → 恢复充电")
        print("[i] 你的电池当前100% — 预期: 停止涓流补充充电, 电量慢慢自然下降")
    else:
        ec.wr(F['LIDF'], cur & ~0x02 & 0xFF)
        print("[+] 已清 LIDF bit1 (解除80%模式, 恢复正常充电)")
    time.sleep(2)
    new = ec.rd(F['LIDF'])[0]
    chg = i2ec.read(CHARGE_CTRL)
    print(f"[i] 2秒后: LIDF=0x{new:02X} ({'bit1保持' if new & 2 else 'bit1被EC清除!'})"
          f"  充电位0x581=0x{chg:02X} ({'已停充' if chg & 0x20 else '充电中/未干预'})")
    if on and not (new & 2):
        print("[!] LIDF bit1被EC清除了 — 状态机不认主机直写, 此路不通")
    elif on and (new & 2):
        print("[✓] LIDF bit1 保持! 继续观察0x581是否翻转为停充(0x28)")


def cmd_watch(ec):
    """循环监控关键充电寄存器 (Ctrl+C退出)"""
    print("监控中: LIDF=80%模式标志  0x581=充电位(bit5)  BAST=电池状态  Ctrl+C退出")
    try:
        while True:
            lidf = ec.rd(F['LIDF'])[0]
            chg = i2ec.read(CHARGE_CTRL)
            bast = ec.rd(F['BAST'])[0] & 0xF
            pct = ec.rd(F['BRSC'])[0]
            pwr = ec.u16(0x48D)
            print(f"\rLIDF=0x{lidf:02X}({'武装' if lidf & 2 else '--'}) "
                  f"chg=0x{chg:02X}({'停充' if chg & 0x20 else '充許'}) "
                  f"BAST={bast} 电量={pct:3d}% 功率={pwr:5d}mW  ", end='', flush=True)
            time.sleep(2)
    except KeyboardInterrupt:
        print()


def cmd_hold80(ec, dur):
    """终极压制: 同时写 LIDF=0x06(bit1+bit2) 和 7天计时器=已到期值
    - bit1直通路径: 状态机见bit1=1 → 电池>=81% → 停充
    - bit2+计时器路径: 状态机见bit2=1且计时器>=604800 → EC自己置bit1("Switch Bat 80%")
    计时器0x5C2-0x5C5为大端序(MSB在前), 写0x00093A81(=604801, 刚过阈值)"""
    import threading
    stop = threading.Event()
    stats = {'wr': 0, 'seen': 0}

    # 计时器写入字节: 604801 = 0x00093A81 → 0x5C2=00, 0x5C3=09, 0x5C4=3A, 0x5C5=81
    TIMER_BYTES = [(0x05C2, 0x00), (0x05C3, 0x09), (0x05C4, 0x3A), (0x05C5, 0x81)]

    def spam():
        while not stop.is_set():
            try:
                ec.wr(F['LIDF'], 0x06)          # bit1+bit2
                for a, v in TIMER_BYTES:          # 计时器=已到期
                    i2ec.write(a, v)
                stats['wr'] += 1
            except Exception:
                pass
            stop.wait(0.15)

    t = threading.Thread(target=spam, daemon=True)
    t.start()
    print("[hold80+] 以~7Hz压制: LIDF=0x06 + 计时器=604801(已到期)")
    print("          状态机无论走哪条路径都应激活80%维护模式 (>=81%停充)")
    print("          Ctrl+C 停止 | 预期观察: chg=0x28 / BAST变化 / LIDF出现0x07(EC自写)")
    start = time.time()
    try:
        while dur == 0 or time.time() - start < dur:
            time.sleep(5)
            try:
                chg = i2ec.read(CHARGE_CTRL)
                lidf = ec.rd(F['LIDF'])[0]
                bast = ec.rd(F['BAST'])[0] & 0xF
                pct = ec.rd(F['BRSC'])[0]
                pwr = ec.u16(0x48D)
                tm = i2ec.read(0x05C2)
                mark = ' ★★★停充生效!' if (chg & 0x20) else ''
                if chg & 0x20:
                    stats['seen'] += 1
                print(f"  [{int(time.time()-start):4d}s] 写{stats['wr']}次 LIDF=0x{lidf:02X} "
                      f"chg=0x{chg:02X} BAST={bast} 电量={pct}% 功率={pwr}mW T={tm:02X}{mark}")
            except Exception as e:
                print(f"  读错误: {e}")
    except KeyboardInterrupt:
        pass
    finally:
        stop.set(); t.join(timeout=1)
        ec.wr(F['LIDF'], 0x00)
        print(f"\n[hold80+] 停止. 停充生效采样: {stats['seen']}次")
        time.sleep(2)
        try:
            chg = i2ec.read(CHARGE_CTRL)
            print(f"[i] 退出2秒后: chg=0x{chg:02X} "
                  f"({'仍停充—状态被EC锁存' if chg & 0x20 else '已恢复(0x581被刷新)'})")
        except Exception as e:
            print(e)


def main():
    if os.geteuid() != 0:
        print("请用 sudo 运行 (需要 /dev/mem 和 /dev/port)")
        sys.exit(1)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    ec = ECWindow()
    if cmd == "status":
        cmd_status(ec)
    elif cmd == "fnlock":
        if len(sys.argv) < 3 or sys.argv[2] not in ("on", "off"):
            print("用法: fnlock on|off"); sys.exit(1)
        cmd_fnlock(ec, sys.argv[2] == "on")
    elif cmd == "mode":
        print(f"当前性能模式: {ec.rd(F['FCMO'])[0]}")
    elif cmd == "setmode":
        n = int(sys.argv[2]) & 3
        if ec.rd(F['OSFG'])[0] == 0:
            ec.wr(F['OSFG'], 1)
        ec.wr(F['FCMI'], 0x80 | n)
        print(f"[+] 已写模式切换命令 0x{0x80 | n:02X} (模式{n})")
        time.sleep(0.5)
        print(f"[i] 当前FCMO: {ec.rd(F['FCMO'])[0]}")
    elif cmd == "battery":
        cmd_battery(ec)
    elif cmd == "charge":
        cmd_charge(ec, sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "arm80":
        cmd_arm80(ec, True)
    elif cmd == "disarm80":
        cmd_arm80(ec, False)
    elif cmd == "watch":
        cmd_watch(ec)
    elif cmd == "hold80":
        dur = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        cmd_hold80(ec, dur)
    elif cmd == "i2ec":
        op = sys.argv[2]
        addr = int(sys.argv[3], 0)
        if op == "rd":
            print(f"XRAM 0x{addr:04X} = 0x{i2ec.read(addr):02X}")
        elif op == "wr":
            i2ec.write(addr, int(sys.argv[4], 0))
            print(f"已写 XRAM 0x{addr:04X} = 0x{int(sys.argv[4], 0):02X}, 回读: 0x{i2ec.read(addr):02X}")
        else:
            print("用法: i2ec rd|wr <addr> [val]")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
